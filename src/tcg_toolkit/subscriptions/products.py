"""Read a subscription's current public offer from its exact merchant product page."""
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..catalog.errors import ProductSourceError
from ..catalog.identity import favorite_source, normalize_favorite_url
from ..catalog.models import Game, SearchSource, StockStatus
from ..catalog.pagination import _text
from ..catalog.providers.cardmax import CardMaxSearcher, CardMaxSEVSearcher, CardMaxWSSearcher, card_details, read_stock
from ..catalog.providers.hobbystation import HobbyStationClient, hobby_number, hobby_rarity, hobby_stock
from ..catalog.providers.yuyutei_products import fetch_product_snapshot as yuyutei_snapshot


def _hobby_offer(page, game, url):
    detail = page.select_one('.detailWrap .detail')
    price_area = detail.select_one('dl.price') if detail else None
    if price_area is None:
        raise ValueError('Missing Hobby Station offer')
    price = re.fullmatch(r'([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*円', _text(price_area.select_one('dd > span')))
    number = _text(detail.find('div', recursive=False))
    name = _text(detail.select_one('.itemName h2'))
    label, status, quantity = hobby_stock(price_area)
    if not number or not name or not price or status == StockStatus.UNKNOWN:
        raise ValueError('Incomplete Hobby Station offer')
    image = page.select_one('.detailWrap .sliderBox img[src]')
    return dict(name=name, card_number=hobby_number(game, number), rarity=hobby_rarity(name),
                price=int(price[1].replace(',', '')), stock_status=status.value, stock_quantity=quantity,
                stock_text=label, image_url=urljoin(url, image['src']) if image else None)


def _cardmax_offer(page, game, url):
    detail = page.select_one('#M_itemInfo')
    canonical = page.select_one('link[rel=canonical][href]')
    if not detail or not canonical or urljoin(url, canonical['href']).rstrip('/') != url:
        raise ValueError('Missing Card Max product')
    price = re.fullmatch(r'販売価格\s*[:：]\s*[￥¥]\s*([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*[（(]税込[)）]',
                         _text(detail.select_one('.price')))
    term = next((dt for dt in detail.select('.detail-info dt') if _text(dt) == 'レアリティ'), None)
    rarity = _text(term.find_next_sibling('dd')) if term else ''
    provider = CardMaxWSSearcher if game == Game.WS else CardMaxSEVSearcher
    name, number, rarity = card_details(_text(page.select_one('.product-info h2:not(.M_title)')), rarity, provider.card_number)
    status, quantity, label = read_stock(detail)
    if not name or not price or status == StockStatus.UNKNOWN:
        raise ValueError('Incomplete Card Max offer')
    image = page.select_one('meta[property="og:image"][content]')
    return dict(name=name, card_number=number, rarity=rarity, price=int(price[1].replace(',', '')),
                stock_status=status.value, stock_quantity=quantity, stock_text=label,
                image_url=urljoin(url, image['content']) if image else None)


def fetch_product_snapshot(game, product_url, client_factory=None):
    game = Game(game)
    source = favorite_source(product_url)
    url = normalize_favorite_url(source, game, product_url)
    if source == SearchSource.YUYUTEI:
        return yuyutei_snapshot(game, url, client_factory)
    is_hobby = source == SearchSource.HOBBYSTATION
    factory = client_factory or (HobbyStationClient if is_hobby else CardMaxSearcher.client_type)
    client = factory()
    try:
        response = client.get(url, timeout=20)
        response.raise_for_status()
        if str(response.url).rstrip('/') != url:
            raise ValueError('Redirected away from the subscribed product')
        html = response.text if is_hobby else CardMaxSearcher.response_html(response)
        page = BeautifulSoup(html, 'html.parser')
        offer = _hobby_offer(page, game, url) if is_hobby else _cardmax_offer(page, game, url)
        return dict(game=game.value, source=source.value, product_url=url, currency='JPY', **offer)
    except httpx.TimeoutException:
        raise ProductSourceError('product_timeout') from None
    except httpx.HTTPError:
        raise ProductSourceError('product_unavailable') from None
    except ValueError:
        raise ProductSourceError('product_unreadable') from None
    finally:
        client.close()
