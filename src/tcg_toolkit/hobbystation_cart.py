"""Request-scoped access to Hobby Station's sales cart."""
from contextlib import contextmanager
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import cart_operations
from .auth.dependencies import current_user, write_guard
from .external.search_proxy import YuYuTeiProxyTransport
from .search import HobbyStationClient


HOST = HobbyStationClient.relay_host
BASE = f'https://{HOST}'
CART = BASE + '/cart'
PRODUCT = re.escape(BASE) + r'/[a-z0-9_-]+/product/detail/([0-9]+)'
SECRET = r'^[A-Za-z0-9%+/_=.,-]+$'
router = APIRouter(prefix='/api/hobbystation/cart', dependencies=[Depends(current_user), Depends(write_guard)])


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    eccube: str = Field(min_length=1, max_length=512, pattern=SECRET, repr=False)
    eccube_bot_check: str | None = Field(default=None, min_length=1, max_length=512, pattern=SECRET, repr=False)


class CartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    credentials: Credentials


class AddRequest(CartRequest):
    product_url: str = Field(pattern='^' + PRODUCT + '$', max_length=512)
    counter: int = Field(default=1, strict=True, ge=1, le=99)


class RowRequest(CartRequest):
    product_class_id: str = Field(pattern=r'^[0-9]+$', max_length=32)


class QuantityRequest(RowRequest):
    quantity: int = Field(strict=True, ge=0, le=99)


def error(kind='invalid_response', status=502, **details):
    return HTTPException(status, detail={'type': 'hobbystation_' + kind, **details})


@contextmanager
def cart_session(credentials):
    with cart_operations.session_lock('hs:' + credentials.eccube), httpx.Client(
        timeout=20, follow_redirects=False, trust_env=False,
        transport=YuYuTeiProxyTransport(relay_host=HOST),
        headers={'Cookie': 'eccube=' + credentials.eccube},
    ) as client:
        yield client


def fetch(client, method, url, *, expected=200, **kwargs):
    try:
        response = client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        raise error('timeout', 504, outcome='unknown') from None
    except httpx.HTTPError:
        raise error('unavailable', outcome='unknown') from None
    # An expired session can be replaced by an empty guest cart with HTTP 200.
    original = client.headers['Cookie'].removeprefix('eccube=')
    if response.status_code in (401, 403, 419) or any(
        c.name == 'eccube' and c.value != original for c in response.cookies.jar
    ):
        raise error('credentials_rejected', 409)
    if response.status_code != expected:
        raise error('unavailable', outcome='unknown')
    return response


def row_item(row):
    link = row.select_one('.ec-cartRow__name a[href]')
    remove = row.select_one('.ec-cartRow__delColumn a[href]')
    amount = row.select_one('.ec-cartRow__amount')
    price = row.select_one('.ec-cartRow__unitPrice')
    if not all((link, remove, amount, price)):
        raise ValueError('Missing cart fields')
    url = urljoin(BASE, link['href'])
    product = re.fullmatch(PRODUCT, url)
    sku = re.fullmatch(re.escape(CART) + r'/remove/([0-9]+)', urljoin(BASE, remove['href']))
    yen = re.fullmatch(r'([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*円', price.get_text(strip=True))
    count = amount.get_text(strip=True)
    if not (product and sku and yen and re.fullmatch(r'[1-9][0-9]*', count)):
        raise ValueError('Unrecognized cart row')
    image = row.select_one('.ec-cartRow__img img[src]')
    return {'product_id': product[1], 'product_class_id': sku[1], 'product_url': url,
            'name': link.get_text(' ', strip=True), 'image_url': urljoin(BASE, image['src']) if image else None,
            'quantity': int(count), 'unit_price': int(yen[1].replace(',', '')), 'currency': 'JPY',
            'removable': bool(remove.get('token-for-anchor') and remove.get('data-method') == 'put')}


def read_cart(client, *, strict=True):
    page = BeautifulSoup(fetch(client, 'GET', CART).text, 'html.parser')
    rows = page.select('.ec-cartRole .ec-cartRow')
    if not rows and not any('現在カート内に商品はございません' in node.get_text(strip=True)
                            for node in page.select('.ec-cartRole .ec-alert-warning__text')):
        raise error()
    items, warnings = [], []
    for index, row in enumerate(rows):
        try:
            items.append(row_item(row))
        except ValueError:
            if strict:
                raise error() from None
            warnings.append({'row_index': index, 'code': 'unrecognized_cart_item'})
    return page, cart_operations.cart_result({'items': items, 'complete': not warnings, 'warnings': warnings})


def quantity(cart, sku):
    counts = [row['quantity'] for row in cart['items'] if row['product_class_id'] == sku]
    if len(counts) > 1:
        raise error('cart_item_ambiguous', 409)
    return counts[0] if counts else 0


def apply_action(client, page, action, sku=None):
    url = CART + ('/clear' if action == 'clear' else f'/{action}/{sku}')
    method = 'post' if action == 'clear' else 'put'
    tokens = {a['token-for-anchor'] for a in page.select('a[href][token-for-anchor][data-method]')
              if urljoin(BASE, a['href']) == url and a['data-method'] == method and a['token-for-anchor']}
    if len(tokens) != 1:
        raise error('cart_action_unavailable', 409)
    with verify_write():
        response = fetch(client, 'POST', url, expected=302, data={'_method': method, '_token': tokens.pop()})
        if urljoin(BASE, response.headers.get('Location', '')) != CART:
            raise error()
        return read_cart(client)


@contextmanager
def verify_write():
    # A POST may have taken effect even if its response or subsequent cart page is invalid.
    try:
        yield
    except HTTPException as exc:
        raise HTTPException(exc.status_code, detail={'outcome': 'unknown', **exc.detail}) from None


def verified(cart, sku, expected):
    actual = quantity(cart, sku)
    if actual != expected:
        raise error('quantity_mismatch', 409, verification='mismatch', after_quantity=actual,
                    expected_quantity=expected, outcome='unknown')
    return actual


def add_product(body):
    product_id = body.product_url.rsplit('/', 1)[1]
    url = BASE + '/products/add_cart/' + product_id
    with cart_session(body.credentials) as client:
        headers = {}
        if body.credentials.eccube_bot_check:
            headers['Cookie'] = client.headers['Cookie'] + '; eccube_bot_check=' + body.credentials.eccube_bot_check
        page = BeautifulSoup(fetch(client, 'GET', body.product_url, headers=headers).text, 'html.parser')
        if page.title and '安全な接続を確認中' in page.title.get_text():
            raise error('product_access_check', 409, outcome='not_sent')
        forms = [f for f in page.select('form[action]') if urljoin(BASE, f['action']) == url
                 and f.get('method', '').lower() == 'post']
        if len(forms) != 1:
            raise error('add_form_unavailable', 409, outcome='not_sent')
        fields = {i['name']: i.get('value', '') for i in forms[0].select('input[name]')}
        sku = fields.get('ProductClass', '')
        if fields.get('product_id') != product_id or not re.fullmatch(r'[0-9]+', sku) or not fields.get('_token'):
            raise error(outcome='not_sent')
        _, cart = read_cart(client)
        before = quantity(cart, sku)
        with verify_write():
            response = fetch(client, 'POST', url, data={'product_id': product_id, 'ProductClass': sku,
                             '_token': fields['_token'], 'quantity': body.counter},
                             headers={'X-Requested-With': 'XMLHttpRequest'})
            try:
                result = response.json()
            except ValueError:
                raise error() from None
            if isinstance(result, dict) and result.get('done') is False:
                messages = result.get('messages')
                if isinstance(messages, list):
                    for message in messages:
                        if not isinstance(message, str):
                            continue
                        if '販売制限数を超える購入はできません' in message:
                            raise error('stock_exceeded', 409, reason='purchase_limit')
                        if '在庫が不足しております' in message:
                            raise error('stock_exceeded', 409, reason='stock')
                raise error('cart_rejected', 409)
            if not isinstance(result, dict) or result.get('done') is not True:
                raise error()
            _, cart = read_cart(client)
            after = verified(cart, sku, before + body.counter)
        return {'status': 'SUCCESS', 'product_id': product_id, 'product_class_id': sku,
                'requested_quantity': body.counter, 'before_quantity': before, 'after_quantity': after,
                'observed_delta': after - before, 'verification': 'verified', 'cart': cart}


@router.post('')
def add(body: AddRequest, request: Request, user=Depends(current_user)):
    parameters = {'source': 'hobbystation', **body.model_dump(exclude={'credentials'})}
    return cart_operations.run_add(request, user['id'], parameters, lambda: add_product(body))


@router.post('/query')
def listing(body: CartRequest):
    with cart_session(body.credentials) as client:
        _, cart = read_cart(client, strict=False)
        return cart


@router.put('/quantity')
def set_quantity(body: QuantityRequest):
    sku = body.product_class_id
    with cart_session(body.credentials) as client:
        page, cart = read_cart(client)
        before = current = quantity(cart, sku)
        if not before and body.quantity:
            raise error('cart_item_not_found', 404)
        while current != body.quantity:
            if not body.quantity:
                action, expected = 'remove', 0
            elif current < body.quantity:
                action, expected = 'up', current + 1
            else:
                action, expected = 'down', current - 1
            try:
                page, cart = apply_action(client, page, action, sku)
                current = verified(cart, sku, expected)
            except HTTPException as exc:
                raise HTTPException(exc.status_code, detail={**exc.detail, 'before_quantity': before,
                                    'last_verified_quantity': current, 'requested_quantity': body.quantity}) from None
        return {'status': 'SUCCESS', 'product_class_id': sku, 'requested_quantity': body.quantity,
                'before_quantity': before, 'after_quantity': current, 'cart': cart}


@router.delete('')
def remove(body: RowRequest):
    sku = body.product_class_id
    with cart_session(body.credentials) as client:
        page, cart = read_cart(client)
        before = quantity(cart, sku)
        if not before:
            raise error('cart_item_not_found', 404)
        _, cart = apply_action(client, page, 'remove', sku)
        verified(cart, sku, 0)
        return {'status': 'SUCCESS', 'removed': {'product_class_id': sku}, 'removed_quantity': before, 'cart': cart}


@router.delete('/clear')
def clear(body: CartRequest):
    with cart_session(body.credentials) as client:
        page, cart = read_cart(client)
        before = cart['total_quantity']
        if before:
            _, cart = apply_action(client, page, 'clear')
            if cart['items']:
                raise error('clear_not_confirmed', 409, outcome='unknown')
        return {'status': 'SUCCESS', 'removed_quantity': before, 'cart': cart}
