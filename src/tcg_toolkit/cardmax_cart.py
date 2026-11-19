"""Verified writes to the Card Max sales basket, using request-scoped member cookies."""
from contextlib import contextmanager
import re
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import cart_operations
from .auth.dependencies import current_user, write_guard
from .cardmax_search import read_stock

BASE = 'https://www.cardmax.jp'
BASKET = BASE + '/shop/basket.html'
PRODUCT = re.escape(BASE) + r'/shopdetail/([0-9]+)/?'
COOKIE_VALUE = r'[A-Za-z0-9%+/_=.,@-]+'
router = APIRouter(prefix='/api/cardmax/cart', dependencies=[Depends(current_user), Depends(write_guard)])


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tempid: str = Field(pattern='^' + COOKIE_VALUE + '$', max_length=512, repr=False)
    login_id: str = Field(pattern='^' + COOKIE_VALUE + '$', max_length=512, repr=False)
    member_key: str = Field(pattern=r'^[0-9]{1,32}_key=[A-Za-z0-9%+/_=.,@-]{1,512}$', repr=False)


class CartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    credentials: Credentials


class AddRequest(CartRequest):
    product_url: str = Field(pattern='^' + PRODUCT + '$', max_length=512)
    counter: int = Field(default=1, strict=True, ge=1, le=99)


class RowRequest(CartRequest):
    brandcode: str = Field(pattern=r'^[0-9]+$', max_length=32)


class QuantityRequest(RowRequest):
    quantity: int = Field(strict=True, ge=0, le=99)


def error(kind='invalid_response', status=502, **fields):
    return HTTPException(status, detail={'type': 'cardmax_' + kind, **fields})


@contextmanager
def session(credentials):
    cookie = f'tempid={credentials.tempid}; login_id={credentials.login_id}; {credentials.member_key}'
    with cart_operations.session_lock('cardmax:' + credentials.tempid), httpx.Client(
        timeout=20, follow_redirects=False, trust_env=False, headers={'Cookie': cookie}
    ) as client:
        yield client


def fetch(client, url, *, fields=None):
    try:
        if fields is None:
            response = client.get(url)
        else:
            response = client.post(url, content=urlencode(fields, encoding='euc_jis_2004').encode('ascii'),
                                   headers={'Content-Type': 'application/x-www-form-urlencoded'})
    except httpx.TimeoutException:
        raise error('timeout', 504) from None
    except httpx.HTTPError:
        raise error('unavailable') from None
    if response.status_code in (401, 403):
        raise error('credentials_rejected', 409)
    if response.status_code != 200:
        raise error('unavailable')
    return BeautifulSoup(response.content.decode('euc_jis_2004', errors='replace'), 'html.parser')


def field_value(node):
    if node.name == 'select':
        option = node.select_one('option[selected]') or node.select_one('option')
        return option.get('value', option.get_text(strip=True)) if option else ''
    return node.get_text() if node.name == 'textarea' else node.get('value', '')


def payload(form, overrides, *, page_url=BASKET):
    if (form is None or form.get('method', '').lower() != 'post'
            or urljoin(page_url, form.get('action', '')) != BASKET):
        raise error()
    # The storefront includes duplicate fields. Preserve browser order and post the fresh row options.
    pairs = []
    for node in form.select('input[name], select[name], textarea[name]'):
        kind = node.get('type', '').lower()
        if node.has_attr('disabled') or kind in ('button', 'submit', 'image', 'reset'):
            continue
        if kind in ('radio', 'checkbox') and not node.has_attr('checked'):
            continue
        name = node['name']
        pairs.append((name, overrides.get(name, field_value(node))))
    if not overrides.keys() <= {name for name, _ in pairs}:
        raise error()
    return pairs


def parse_row(form):
    brand = form.select_one('input[name=brandcode]')
    amount = form.select_one('[name=amount]')
    link = form.select_one('.basket-name a[href]')
    price = form.select_one('.basket-price')
    if not all((brand, amount, link, price)):
        raise ValueError('Incomplete basket row')
    code = brand.get('value', '')
    url = urljoin(BASE, link['href'])
    match = re.fullmatch(PRODUCT, url)
    count = field_value(amount)
    yen = re.fullmatch(r'([0-9,]+)\s*円', price.get_text(strip=True))
    if not match or match[1] != code or not re.fullmatch(r'[1-9][0-9]*', count) or not yen:
        raise ValueError('Invalid basket row')
    unit, remainder = divmod(int(yen[1].replace(',', '')), int(count))
    if remainder:
        raise ValueError('Non-integral unit price')
    image = form.select_one('.basket-img img[src]')
    return {'brandcode': code, 'name': link.get_text(' ', strip=True), 'product_url': url.rstrip('/'),
            'image_url': urljoin(BASE, image['src']) if image else None, 'quantity': int(count),
            'unit_price': unit, 'currency': 'JPY', 'removable': True}


def read_cart(client, credentials, *, strict=True):
    page = fetch(client, BASKET)
    basket = page.select_one('#basket table.basket')
    token = page.select_one('form[name=sslform] input[name=ssl_tempid]')
    if basket is None or token is None:
        raise error()
    if token.get('value') != credentials.tempid or not page.select_one('a[href*="/shop/logout.html"]'):
        raise error('credentials_rejected', 409)
    rows = basket.select('form[id^=forms]')
    if not rows and '買い物かごに商品がありません' not in basket.get_text(strip=True):
        raise error()
    items, warnings = [], []
    for index, form in enumerate(rows):
        try:
            items.append(parse_row(form))
        except ValueError:
            if strict:
                raise error() from None
            warnings.append({'row_index': index, 'code': 'unrecognized_cart_item'})
    cart = cart_operations.cart_result({'items': items, 'warnings': warnings, 'complete': not warnings})
    return page, cart


def count(cart, brand):
    matches = [i['quantity'] for i in cart['items'] if i['brandcode'] == brand]
    if len(matches) > 1:
        raise error('cart_item_ambiguous', 409)
    return matches[0] if matches else 0


def post_and_read(client, credentials, fields):
    try:
        fetch(client, BASKET, fields=fields)
        return read_cart(client, credentials)[1]
    except HTTPException as exc:
        raise HTTPException(exc.status_code, detail={**exc.detail, 'outcome': 'unknown'}) from None


def verified(cart, brand, expected):
    actual = count(cart, brand)
    if actual != expected:
        raise error('quantity_mismatch', 409, after_quantity=actual, expected_quantity=expected,
                    outcome='unknown', verification='mismatch')


def row_payload(basket, brand, target):
    form = basket.select_one(f'#basket form[id^=forms] input[name=brandcode][value="{brand}"]').find_parent('form')
    changes = {'type': 'upd', 'amount': str(target)} if target else {'type': 'del'}
    return payload(form, changes)


@router.post('/query')
def listing(body: CartRequest):
    with session(body.credentials) as client:
        return read_cart(client, body.credentials, strict=False)[1]


def add_product(body):
    brand = body.product_url.rstrip('/').rsplit('/', 1)[1]
    with session(body.credentials) as client:
        basket, cart = read_cart(client, body.credentials)
        before = count(cart, brand)
        after = before + body.counter
        page = fetch(client, body.product_url)
        form = page.select_one('form[name=form1]')
        if form is None or not form.select_one('[name=amount]'):
            raise error('add_form_unavailable', 409, outcome='not_sent')
        if form.select_one('[name=brand_option], [name=spcode] option:not([value=""])'):
            raise error('option_required', 409, outcome='not_sent')
        _, available, _ = read_stock(page)
        if available is not None and after > available:
            raise error('stock_exceeded', 409, outcome='not_sent')
        if before:
            # Card Max rejects a second product-form add for a row already in the basket.
            fields = row_payload(basket, brand, after)
        else:
            changes = {'amount': str(body.counter)}
            if form.select_one('[name=ordertype]'):
                changes['ordertype'] = ''  # Ordinary add, as opposed to the storefront's buy-now action.
            fields = payload(form, changes, page_url=body.product_url)
            if {v for k, v in fields if k == 'brandcode'} != {brand} or ('admin_id', 'cardmax') not in fields:
                raise error()
        cart = post_and_read(client, body.credentials, fields)
        verified(cart, brand, after)
        return {'status': 'SUCCESS', 'brandcode': brand, 'before_quantity': before, 'after_quantity': after,
                'requested_quantity': body.counter, 'observed_delta': body.counter, 'verification': 'verified', 'cart': cart}


@router.post('')
def add(body: AddRequest, request: Request, user=Depends(current_user)):
    parameters = {'source': 'cardmax', **body.model_dump(exclude={'credentials'})}
    return cart_operations.run_add(request, user['id'], parameters, lambda: add_product(body))


def change_row(body, target, *, require_present=False):
    with session(body.credentials) as client:
        page, cart = read_cart(client, body.credentials)
        brand = body.brandcode
        before = count(cart, brand)
        if before == 0 and (require_present or target):
            raise error('cart_item_not_found', 404)
        if before != target:
            cart = post_and_read(client, body.credentials, row_payload(page, brand, target))
            verified(cart, brand, target)
        return {'status': 'SUCCESS', 'brandcode': brand, 'before_quantity': before,
                'requested_quantity': target, 'after_quantity': target, 'cart': cart}


@router.put('/quantity')
def set_quantity(body: QuantityRequest):
    return change_row(body, body.quantity)


@router.delete('')
def remove(body: RowRequest):
    result = change_row(body, 0, require_present=True)
    return {'status': 'SUCCESS', 'removed': {'brandcode': body.brandcode},
            'removed_quantity': result['before_quantity'], 'cart': result['cart']}


@router.delete('/clear')
def clear(body: CartRequest):
    with session(body.credentials) as client:
        page, cart = read_cart(client, body.credentials)
        before = cart['total_quantity']
        if before:
            fields = payload(page.select_one('form[name=delform]'), {'brandcode': 'all', 'type': 'del'})
            cart = post_and_read(client, body.credentials, fields)
            if cart['items']:
                raise error('clear_not_confirmed', 409, outcome='unknown')
        return {'status': 'SUCCESS', 'removed_quantity': before, 'cart': cart}
