"""Request-scoped YuYu-Tei credentials and verified, single-attempt cart operations."""
from datetime import datetime, timezone
from contextlib import contextmanager
from hashlib import sha256
from hmac import compare_digest
import json
import re
from threading import Lock
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .auth.dependencies import current_user, write_guard
from .cart_models import CartTarget
from .product_identity import GAME_PATHS
from . import cart_operations
from .external.search_proxy import YuYuTeiProxyTransport

router = APIRouter(prefix='/api/yuyutei', dependencies=[Depends(current_user)])
CART_URL = 'https://yuyu-tei.jp/api/cart_order_edit'
CART_PAGE = 'https://yuyu-tei.jp/cart/sell'
CART_SERVICE = 'https://yuyu-tei.jp/api/cart_service'
_session_locks = {}
_session_locks_guard = Lock()


@contextmanager
def session_lock(csrf_token):
    # Laravel may re-encrypt its cookie without changing the session. The CSRF
    # token stays stable within that session, including across local accounts.
    key = sha256(csrf_token.encode()).digest()
    with _session_locks_guard:
        entry = _session_locks.setdefault(key, [Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _session_locks_guard:
            entry[1] -= 1
            if entry[1] == 0:
                del _session_locks[key]


@contextmanager
def cart_session(session_cookie, csrf_token):
    # Reads also hold the lock: rendering the cart can refresh its row mapping.
    with session_lock(csrf_token), httpx.Client(timeout=20, follow_redirects=False, trust_env=False,
                                                   transport=YuYuTeiProxyTransport()) as client:
        client.cookies.set('laravel_session', session_cookie, domain='yuyu-tei.jp', path='/')
        yield client


class CredentialsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cookie: str = Field(min_length=1, max_length=16384, repr=False)
    csrf_token: str = Field(min_length=1, max_length=4096, repr=False)

    @field_validator('cookie', 'csrf_token')
    @classmethod
    def clean_secret(cls, value, info):
        if '\r' in value or '\n' in value:
            raise ValueError('Invalid credential')
        value = value.strip()
        if info.field_name == 'cookie' and ('laravel_session=' in value or ';' in value):
            matches = [part.strip().partition('=')[2] for part in value.split(';')
                       if part.strip().partition('=')[0] == 'laravel_session']
            if len(matches) != 1:
                raise ValueError('Expected one laravel_session cookie')
            value = matches[0]
        if not re.fullmatch(r'[A-Za-z0-9%+/_=.\-]+', value):
            raise ValueError('Invalid credential')
        return value


class CartInput(CartTarget):
    counter: int = Field(default=1, strict=True, ge=1, le=99)


class RemoveInput(CartTarget):
    type: Literal['normal', 'reserved'] = 'normal'


class QueryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    credentials: CredentialsInput = Field(repr=False)


class AddRequest(CartInput):
    credentials: CredentialsInput = Field(repr=False)


class RemoveRequest(RemoveInput):
    credentials: CredentialsInput = Field(repr=False)


async def parse_input(request, model):
    # Every body now contains secrets. Avoid FastAPI's default validation output,
    # which includes raw input even for malformed JSON or unrelated invalid fields.
    try:
        return model.model_validate(await request.json())
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(422, detail='invalid_yuyutei_request') from None


async def query_input(request: Request):
    return await parse_input(request, QueryInput)


async def add_input(request: Request):
    return await parse_input(request, AddRequest)


async def remove_input(request: Request):
    return await parse_input(request, RemoveRequest)


def post_add(client, body, csrf_token):
    payload = {**body.model_dump(), 'mode': 'add', 'type': 'sell',
               'time': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')}
    referer = f'https://yuyu-tei.jp/sell/{GAME_PATHS[body.gid]}/card/{body.ver}/{body.cid}'
    response = cart_request(client, 'POST', CART_URL, data=payload,
                            headers={'X-CSRF-TOKEN': csrf_token, 'Referer': referer})
    try:
        data = response.json()
    except ValueError:
        data = None
    status = data.get('status') if isinstance(data, dict) else None
    if status == 'SUCCESS':
        return
    if isinstance(status, str) and re.fullmatch(r'ERROR[0-9]*', status):
        raise HTTPException(409, detail={'type': 'yuyutei_cart_rejected', 'code': status})
    raise cart_error()


def add_to_cart(body: CartInput, session_cookie: str, csrf_token: str):
    target = body.model_dump(exclude={'counter'})
    def quantity(snapshot):
        return sum(item['quantity'] for item in snapshot['items']
                   if all(item[key] == value for key, value in target.items()))
    with cart_session(session_cookie, csrf_token) as client:
        # Missing baseline means no POST is sent. Deletion controls are irrelevant
        # to counting, but every row must be readable to verify an increment.
        try:
            before = quantity(read_cart(client, csrf_token))
        except HTTPException as exc:
            raise HTTPException(exc.status_code, detail={**exc.detail, 'outcome': 'not_sent'}) from None
        try:
            post_add(client, body, csrf_token)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, detail={**exc.detail, 'requested_quantity': body.counter,
                'before_quantity': before, 'after_quantity': None, 'verification': 'unknown'}) from None
        result = {**body.model_dump(), 'requested_quantity': body.counter, 'before_quantity': before}
        try:
            snapshot = read_cart(client, csrf_token)
            after = quantity(snapshot)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, detail={**exc.detail, **result, 'outcome': 'unknown',
                'upstream_status': 'SUCCESS', 'after_quantity': None, 'verification': 'unknown'}) from None
        result.update(after_quantity=after, observed_delta=after - before)
        if after - before != body.counter:
            raise HTTPException(409, detail={**result, 'type': 'yuyutei_quantity_mismatch',
                'upstream_status': 'SUCCESS', 'outcome': 'unknown', 'verification': 'mismatch'})
        return write_result(snapshot, **result, verification='verified')


@router.post('/cart', dependencies=[Depends(write_guard)])
def add(request: Request, envelope=Depends(add_input), user=Depends(current_user)):
    body = CartInput.model_validate(envelope.model_dump(exclude={'credentials'}))
    credentials = envelope.credentials
    key = request.headers.get('idempotency-key')
    store = request.app.state.auth.store
    if key is not None:
        replay = cart_operations.reserve(store, user['id'], key, body.model_dump())
        if replay is not None:
            return replay
    try:
        result = add_to_cart(body, credentials.cookie, credentials.csrf_token)
    except HTTPException as exc:
        if key is not None:
            cart_operations.finish(store, user['id'], key, exc.status_code, {'detail': exc.detail})
        raise
    # Unexpected failures leave a durable processing record rather than allowing
    # a later caller to unknowingly repeat a POST that may already have succeeded.
    if key is not None:
        cart_operations.finish(store, user['id'], key, 200, result)
    return result


def cart_error(kind='yuyutei_invalid_response', status=502):
    return HTTPException(status, detail={'type': kind, 'outcome': 'unknown'})


def cart_request(client, method, url, **kwargs):
    try:
        response = client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        raise cart_error('yuyutei_timeout', 504) from None
    except httpx.HTTPError:
        raise cart_error('yuyutei_unavailable') from None
    if response.status_code in (401, 403, 419):
        raise HTTPException(409, detail={'type': 'yuyutei_credentials_rejected'})
    if response.status_code != 200:
        raise cart_error('yuyutei_unavailable')
    return response


def parse_cart_row(row, require_removal=False):
    if not re.fullmatch(r'item(?:Reserved)?Info[0-9]+', row['id']):
        raise ValueError('Invalid row identifier')
    suffix = re.search(r'[0-9]+$', row['id'])[0]
    kind = 'reserved' if row['id'].startswith('itemReserved') else 'normal'
    link = next((a for a in row.select('a[href]') if a.get_text(strip=True)), None)
    if link is None:
        raise ValueError('Missing product')
    url = urlsplit(urljoin(CART_PAGE, link['href']))
    product = re.fullmatch(r'/sell/([A-Za-z0-9_-]+)/card/([A-Za-z0-9_.-]+)/([0-9]+)', url.path)
    if url.scheme != 'https' or url.netloc != 'yuyu-tei.jp' or not product:
        raise ValueError('Invalid product')
    game, ver, cid = product.groups()
    def value(prefix):
        node = row.find('input', id=prefix + suffix)
        if node is None:
            raise ValueError('Missing cart field')
        return node.get('value', '')
    identity = re.fullmatch(r'([0-9]+)' + re.escape(ver + cid) + r'([01])', value('itemData'))
    if identity is None:
        raise ValueError('Invalid product identity')
    item_id = 'itemValue' + suffix
    action = re.compile(r"removeItem\(\s*['\"]" + re.escape(item_id) + r"['\"]\s*,\s*['\"]" + kind + r"['\"]\s*\)")
    removable = any(action.search(a['href']) for a in row.select('a[href]'))
    if require_removal and not removable:
        raise ValueError('Missing removal action')
    # Thumbnail only: optional, never required to read or change the row.
    image = next((img for img in row.select('img') if img.get('src') or img.get('data-src')), None)
    image_url = urljoin(CART_PAGE, (image.get('src') or image.get('data-src')).strip()) if image else None
    if image_url and urlsplit(image_url).scheme != 'https':
        image_url = None
    quantity, price = value('itemValue'), value('itemPrice')
    if not quantity.isascii() or not quantity.isdecimal() or not price.isascii() or not price.isdecimal():
        raise ValueError('Invalid cart amounts')
    return {'gid': int(identity[1]), 'game': game, 'ver': ver, 'cid': cid,
            'kizu': int(identity[2]), 'type': kind, 'name': link.get_text(' ', strip=True),
            'quantity': int(quantity), 'unit_price': int(price), 'currency': 'JPY',
            'product_url': url.geturl(), 'image_url': image_url, 'removable': removable, '_cart_item_id': item_id}


def parse_cart(html, *, strict=True, require_removal=False):
    soup = BeautifulSoup(html, 'html.parser')
    container = soup.select_one('#cart_all')
    if container is None:
        raise ValueError('Unrecognized cart')
    items, warnings = [], []
    rows = [row for row in container.find_all(id=re.compile(r'^item(?:Reserved)?Info'))
            if not re.search(r'display\s*:\s*none', row.get('style', ''), re.I)]
    for index, row in enumerate(rows):
        try:
            items.append(parse_cart_row(row, require_removal=require_removal))
        except ValueError:
            if strict:
                raise
            # Do not reflect upstream HTML or pretend an unreadable row is gone.
            warnings.append({'row_index': index, 'code': 'unrecognized_cart_item'})
    if not rows:
        empty = container.select_one('#cart_empty_meg')
        if empty is None or re.search(r'display\s*:\s*none', empty.get('style', ''), re.I):
            raise ValueError('Cannot confirm empty cart')
    return {'items': items, 'warnings': warnings, 'complete': not warnings}


def read_cart(client, csrf_token, *, strict=True, require_removal=False):
    response = cart_request(client, 'GET', CART_PAGE)
    soup = BeautifulSoup(response.text, 'html.parser')
    tokens = soup.select('meta[name="csrf-token"]')
    if len(tokens) != 1 or not tokens[0].get('content'):
        raise cart_error()
    # An expired/invalid cookie can return HTTP 200 with a fresh anonymous cart.
    # Never adopt that new session or accept its empty cart as deletion evidence.
    if not compare_digest(tokens[0]['content'].encode(), csrf_token.encode()):
        raise HTTPException(409, detail={'type': 'yuyutei_credentials_rejected'})
    try:
        return parse_cart(response.text, strict=strict, require_removal=require_removal)
    except ValueError:
        raise cart_error() from None


def cart_result(snapshot):
    items, complete = snapshot['items'], snapshot['complete']
    return {'items': [{k: v for k, v in item.items() if k != '_cart_item_id'} for item in items],
            'total_quantity': sum(item['quantity'] for item in items) if complete else None,
            'subtotal': sum(item['quantity'] * item['unit_price'] for item in items) if complete else None,
            'currency': 'JPY', 'complete': complete, 'warnings': snapshot['warnings']}


def write_result(snapshot, **fields):
    return {'status': 'SUCCESS', **fields, 'cart': cart_result(snapshot)}


def matching_rows(snapshot, target):
    return [item for item in snapshot['items'] if all(item[key] == value for key, value in target.items())]


def post_cart_service(client, csrf_token, row, *, mode, kind, **fields):
    response = cart_request(client, 'POST', CART_SERVICE,
        data={'mode': mode, 'CART_ITEM_ID': row['_cart_item_id'], 'status': 'sell', 'type': kind, **fields},
        headers={'X-CSRF-TOKEN': csrf_token, 'Referer': CART_PAGE,
                 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
    try:
        data = response.json()
    except ValueError:
        raise cart_error() from None
    if not isinstance(data, dict):
        raise cart_error()
    return data


@router.post('/cart/query', dependencies=[Depends(write_guard)])
def listing(envelope=Depends(query_input)):
    credentials = envelope.credentials
    # Include reads: rendering the upstream cart can refresh its row mapping.
    with cart_session(credentials.cookie, credentials.csrf_token) as client:
        return cart_result(read_cart(client, credentials.csrf_token, strict=False))


def locate(snapshot, target, *, allow_missing=False):
    found = matching_rows(snapshot, target)
    if not found and allow_missing:
        return None
    if not found:
        raise HTTPException(404, detail='yuyutei_cart_item_not_found')
    if len(found) != 1:
        raise HTTPException(409, detail={'type': 'yuyutei_cart_item_ambiguous'})
    return found[0]


def remove_row(client, csrf_token, target, row):
    """Remove one located row; return the verified cart without it. Caller holds the session lock."""
    data = post_cart_service(client, csrf_token, row, mode='remove', kind=target['type'])
    if not isinstance(data, dict) or not {'CART_ITEM_ID', 'GAME_ID', 'RESERVED_CHECK'} <= data.keys() or str(data['GAME_ID']) != str(target['gid']):
        raise cart_error()
    try:
        remaining = read_cart(client, csrf_token, require_removal=True)
    except HTTPException as exc:
        # The POST has already happened: failed verification does not mean
        # the removal failed, and must never invite an automatic retry.
        raise HTTPException(exc.status_code, detail={**exc.detail, 'outcome': 'unknown'}) from None
    if matching_rows(remaining, target):
        raise cart_error('yuyutei_removal_not_confirmed', 409)
    return remaining


@router.delete('/cart', dependencies=[Depends(write_guard)])
def remove(body=Depends(remove_input)):
    credentials = body.credentials
    target = body.model_dump(exclude={'credentials'})
    with cart_session(credentials.cookie, credentials.csrf_token) as client:
        found = locate(read_cart(client, credentials.csrf_token, require_removal=True), target)
        remaining = remove_row(client, credentials.csrf_token, target, found)
        return write_result(remaining, removed=target, removed_quantity=found['quantity'])


class QuantityRequest(RemoveInput):
    quantity: int = Field(strict=True, ge=0, le=98)
    credentials: CredentialsInput = Field(repr=False)


async def quantity_input(request: Request):
    return await parse_input(request, QuantityRequest)


@router.put('/cart/quantity', dependencies=[Depends(write_guard)])
def lower_quantity(body=Depends(quantity_input)):
    # Resolve the current row under the session lock and send only the difference.
    # A target (not a delta) keeps a repeated successful request from lowering twice.
    credentials = body.credentials
    target = body.model_dump(exclude={'credentials', 'quantity'})
    with cart_session(credentials.cookie, credentials.csrf_token) as client:
        snapshot = read_cart(client, credentials.csrf_token, require_removal=True)
        found = locate(snapshot, target, allow_missing=body.quantity == 0)
        if found is None:
            return write_result(snapshot, **target, requested_quantity=0, before_quantity=0, after_quantity=0)
        result = {**target, 'requested_quantity': body.quantity, 'before_quantity': found['quantity']}
        if body.quantity == found['quantity']:
            return write_result(snapshot, **result, after_quantity=found['quantity'])
        if body.quantity > found['quantity']:
            raise HTTPException(409, detail={**result, 'type': 'yuyutei_quantity_increase_unsupported'})
        if body.quantity == 0:
            remaining = remove_row(client, credentials.csrf_token, target, found)
            return write_result(remaining, **result, after_quantity=0)
        try:
            data = post_cart_service(client, credentials.csrf_token, found, mode='add',
                kind='' if target['type'] == 'normal' else target['type'],
                counter=body.quantity - found['quantity'])
            if (not isinstance(data, dict) or data.get('CART_ITEM_ID') != found['_cart_item_id']
                    or data.get('cart_item_result') is not True):
                raise cart_error()
        except HTTPException as exc:
            raise HTTPException(exc.status_code, detail={**result, **exc.detail,
                'outcome': 'unknown', 'after_quantity': None}) from None
        try:
            after = read_cart(client, credentials.csrf_token)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, detail={**result, 'outcome': 'unknown', 'upstream_status': 'SUCCESS',
                                                         'after_quantity': None, 'type': exc.detail['type']}) from None
        matches = matching_rows(after, target)
        count = matches[0]['quantity'] if len(matches) == 1 else (0 if not matches else None)
        if count != body.quantity:
            raise HTTPException(409, detail={**result, 'type': 'yuyutei_quantity_mismatch',
                                             'outcome': 'unknown', 'after_quantity': count})
        return write_result(after, **result, after_quantity=count)
