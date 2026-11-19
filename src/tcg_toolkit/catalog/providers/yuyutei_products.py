"""YuYu-Tei product-page fetching, parsing and printed-detail caching."""
import re
from threading import Lock
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ...external.search_proxy import SearchProxyClient
from ..errors import ProductSourceError
from ..identity import GAME_PATHS, normalize_product_url
from ..models import Game

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


def _fetch_html(product_url, client_factory):
    client = (client_factory or SearchProxyClient)()
    try:
        response = client.get(product_url, timeout=20)
        response.raise_for_status()
    except httpx.TimeoutException:
        raise ProductSourceError('product_timeout') from None
    except httpx.HTTPError:
        raise ProductSourceError('product_unavailable') from None
    finally:
        client.close()
    return response.text


def fetch_product_snapshot(game, product_url, client_factory=None):
    product_url = normalize_product_url(game, product_url)
    html = _fetch_html(product_url, client_factory)
    try:
        return parse_product_snapshot(html, game, product_url)
    except ValueError:
        raise ProductSourceError('product_unreadable') from None


def fetch_product(gid, ver, cid, client_factory=None, *, stock=False, kizu=0):
    key = (gid, ver, cid)
    with _cache_lock:
        if not stock and key in _cache:
            return _cache[key]
    url = f'https://yuyu-tei.jp/sell/{GAME_PATHS[gid]}/card/{ver}/{cid}'
    html = _fetch_html(url, client_factory)
    try:
        details = parse_product(html)
    except ValueError:
        raise ProductSourceError('product_unreadable') from None
    with _cache_lock:
        if len(_cache) >= CACHE_LIMIT:
            _cache.pop(next(iter(_cache)))
        _cache[key] = details
    return {**details, **parse_product_stock(html, kizu)} if stock else details
