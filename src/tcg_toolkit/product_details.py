"""Card number and rarity of a YuYu-Tei product, read from its public product page.

The cart page lists products without these, so the cart UI looks them up here.
No shop credentials are involved; pages are fetched like search pages.
"""
import re
from urllib.parse import urlsplit, urljoin
from threading import Lock
from typing import Annotated

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Query

from .auth.routes import current_user
from .external.search_proxy import SearchProxyClient
from .models import Game

router = APIRouter(prefix='/api/yuyutei', dependencies=[Depends(current_user)])
GAME_PATHS = {7: 'ws', 8: 'chaos', 11: 'pm', 34: 'lo', 47: 'sev', 56: 'wsr'}
GAME_CODES = {game: 'lo' if game == Game.LYCEE else game.value for game in Game}
RARITY = re.compile(r'[A-Za-z0-9+★☆/-]+')
CACHE_LIMIT = 2000
# Printed details never change for a product, so a process-wide cache is safe.
_cache = {}
_cache_lock = Lock()


def parse_product(html):
    soup = BeautifulSoup(html, 'html.parser')
    number = soup.select_one('#product-detail span.border')
    title = soup.select_one('#power h3')
    card_number = ' '.join(number.get_text(' ', strip=True).split()) if number else ''
    if not card_number:
        raise ValueError('Missing card number')
    rarity = None
    if title is not None:
        # The title reads "<rarity> <name>", followed by the kana reading after <br>.
        heading = next((text.strip() for text in title.find_all(string=True, recursive=False) if text.strip()), '')
        token = heading.split(maxsplit=1)[0] if heading else ''
        if RARITY.fullmatch(token):
            rarity = token
    return {'card_number': card_number, 'rarity': rarity}


def parse_product_stock(html, kizu=0):
    soup = BeautifulSoup(html, 'html.parser')
    values = set()
    for node in soup.select('input.cart_active'):
        container = node.find_parent(class_='counter')
        condition = container.select_one('input.cart_kizu') if container else None
        if condition is None or condition.get('value') != str(kizu):
            continue
        value = node.get('value', '')
        if re.fullmatch(r'[0-9]+', value):
            values.add(int(value))
    quantity = next(iter(values)) if len(values) == 1 else None
    return {'stock_quantity': quantity,
            'stock_status': 'unknown' if quantity is None else 'in_stock' if quantity else 'out_of_stock'}


def normalize_product_url(game, value):
    url = urlsplit(value)
    code = GAME_CODES[Game(game)]
    pattern = rf'/sell/{code}/card/([A-Za-z0-9_-][A-Za-z0-9_.-]{{0,63}})/([0-9]{{1,32}})/?'
    if (url.scheme != 'https' or url.netloc.lower() != 'yuyu-tei.jp'
            or url.query or url.fragment or not re.fullmatch(pattern, url.path)):
        raise ValueError('Invalid product URL for this game')
    return 'https://yuyu-tei.jp' + url.path.rstrip('/')


def parse_product_snapshot(html, game, product_url):
    """Read a complete normal-condition offer; missing values are never zero."""
    soup = BeautifulSoup(html, 'html.parser')
    details = parse_product(html)
    offers = set()
    for condition in soup.select('.counter input.cart_kizu[value="0"]'):
        counter = condition.find_parent(class_='counter')
        quantity = counter.select_one('input.cart_active')
        if quantity is None or not re.fullmatch(r'[0-9]+', quantity.get('value', '')):
            raise ValueError('Missing stock quantity')
        # The price and quantity share an offer row; do not read another condition.
        region = counter.parent
        while region is not None and not region.select_one('.lhs h4'):
            region = region.parent
        if region is None or len(region.select('.counter input.cart_kizu')) != 1:
            raise ValueError('Ambiguous offer region')
        price = region.select_one('.lhs h4').get_text(' ', strip=True)
        match = re.fullmatch(r'([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*円', price)
        if match is None:
            raise ValueError('Missing price')
        offers.add((int(match[1].replace(',', '')), int(quantity['value'])))
    if len(offers) != 1:
        raise ValueError('Missing or conflicting normal-condition offers')
    price, quantity = offers.pop()
    title = soup.select_one('#power h3')
    heading = next((str(text).strip() for text in title.find_all(string=True, recursive=False)
                    if str(text).strip()), '') if title else ''
    name = heading.split(maxsplit=1)[-1] if details['rarity'] else heading
    if not name:
        raise ValueError('Missing product name')
    image = soup.select_one('#product-detail img')
    return dict(game=Game(game).value, name=name, **details, price=price, currency='JPY',
                stock_quantity=quantity, stock_status='in_stock' if quantity else 'out_of_stock',
                stock_text=f'在庫 : {quantity} 点', product_url=product_url,
                image_url=urljoin(product_url, image['src']) if image and image.get('src') else None)


def fetch_product_snapshot(game, product_url, client_factory=None):
    product_url = normalize_product_url(game, product_url)
    client = (client_factory or SearchProxyClient)()
    try:
        response = client.get(product_url, timeout=20)
        response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(504, detail='product_timeout') from None
    except httpx.HTTPError:
        raise HTTPException(502, detail='product_unavailable') from None
    finally:
        client.close()
    try:
        return parse_product_snapshot(response.text, game, product_url)
    except ValueError:
        raise HTTPException(502, detail='product_unreadable') from None


def fetch_product(gid, ver, cid, client_factory=None, *, stock=False, kizu=0):
    key = (gid, ver, cid)
    with _cache_lock:
        if not stock and key in _cache:
            return _cache[key]
    url = f'https://yuyu-tei.jp/sell/{GAME_PATHS[gid]}/card/{ver}/{cid}'
    client = (client_factory or SearchProxyClient)()
    try:
        response = client.get(url, timeout=20)
        response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(504, detail='product_timeout') from None
    except httpx.HTTPError:
        raise HTTPException(502, detail='product_unavailable') from None
    finally:
        client.close()
    try:
        details = parse_product(response.text)
    except ValueError:
        raise HTTPException(502, detail='product_unreadable') from None
    with _cache_lock:
        if len(_cache) >= CACHE_LIMIT:
            _cache.pop(next(iter(_cache)))
        _cache[key] = details
    return {**details, **parse_product_stock(response.text, kizu)} if stock else details


@router.get('/product')
def product(gid: Annotated[int, Query()], ver: Annotated[str, Query(min_length=1, max_length=64,
            pattern=r'^[A-Za-z0-9_-][A-Za-z0-9_.-]*$')], cid: Annotated[str, Query(min_length=1, max_length=32,
            pattern=r'^[0-9]+$')], stock: bool = False, kizu: Annotated[int, Query(ge=0, le=1)] = 0):
    if gid not in GAME_PATHS:
        raise HTTPException(422, detail='invalid_product')
    return {'gid': gid, 'ver': ver, 'cid': cid, **fetch_product(gid, ver, cid, stock=stock, kizu=kizu)}
