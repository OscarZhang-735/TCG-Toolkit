"""Read a current offer directly from a Hobby Station or Card Max product page."""
import re
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from ..errors import ProductSourceError
from ..identity import HOBBY_GAME_DIRS, normalize_storefront_url
from ..models import Game, SearchSource
from ..pagination import _text
from .cardmax import CardMaxSearcher, CardMaxSEVSearcher, CardMaxWSSearcher, card_details, read_stock
from .hobbystation import HobbyStationClient, hobby_number, hobby_rarity, hobby_stock


def _hobby_offer(page, url, game):
    detail = page.select_one('.detailWrap .detail')
    area = detail.select_one('dl.price') if detail else None
    if area is None:
        raise ValueError('Missing Hobby Station offer')
    if game is None:
        directory = urlsplit(url).path.split('/')[1]
        game = next((g for g, d in HOBBY_GAME_DIRS.items() if d == directory), None)
    label, status, quantity = hobby_stock(area)
    name = _text(detail.select_one('.itemName h2'))
    number = hobby_number(game, _text(detail.find('div', recursive=False)))
    price = re.fullmatch(r'([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*円', _text(area.select_one('dd > span')))
    image = page.select_one('.detailWrap .sliderBox img[src]')
    return dict(game=game, name=name, card_number=number or None, rarity=hobby_rarity(name),
                price=int(price[1].replace(',', '')) if price else None,
                stock_status=status.value, stock_quantity=quantity, stock_text=label,
                image_url=urljoin(url, image['src']) if image else None)


def _cardmax_offer(page, url, game):
    detail = page.select_one('#M_itemInfo')
    canonical = page.select_one('link[rel=canonical][href]')
    if not detail or not canonical or urljoin(url, canonical['href']).rstrip('/') != url:
        raise ValueError('Missing Card Max product')
    title = _text(page.select_one('.product-info h2:not(.M_title)'))
    if game is None:
        edition = title.partition('◇')[2]
        if re.search(r'【(?:ヴァイスシュヴァルツ|WS)】', edition):
            game = Game.WS
        elif re.search(r'【シャドウバース[・\s]*エボルヴ\s*】', edition):
            game = Game.SEV
    status, quantity, label = read_stock(detail)
    price = re.fullmatch(r'販売価格\s*[:：]\s*[￥¥]\s*([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*[（(]税込[)）]',
                         _text(detail.select_one('.price')))
    term = next((dt for dt in detail.select('.detail-info dt') if _text(dt) == 'レアリティ'), None)
    rarity = _text(term.find_next_sibling('dd')) if term else ''
    name, number = title, None
    if game in (Game.WS, Game.SEV):
        provider = CardMaxWSSearcher if game == Game.WS else CardMaxSEVSearcher
        try:
            name, number, rarity = card_details(title, rarity, provider.card_number)
        except ValueError:
            pass  # The stock marker is independent of the card number / title format.
    image = page.select_one('meta[property="og:image"][content]')
    return dict(game=game, name=name, card_number=number, rarity=rarity or None,
                price=int(price[1].replace(',', '')) if price else None,
                stock_status=status.value, stock_quantity=quantity, stock_text=label,
                image_url=urljoin(url, image['content']) if image else None)


def fetch_storefront_detail(source, product_url, *, game=None, client_factory=None):
    url = normalize_storefront_url(source, product_url)
    is_hobby = source == SearchSource.HOBBYSTATION
    factory = client_factory or (HobbyStationClient if is_hobby else CardMaxSearcher.client_type)
    client = factory()
    try:
        response = client.get(url, timeout=20)
        response.raise_for_status()
        if str(response.url).rstrip('/') != url:
            raise ValueError('Redirected away from product')
        html = response.text if is_hobby else CardMaxSearcher.response_html(response)
        page = BeautifulSoup(html, 'html.parser')
        parse = _hobby_offer if is_hobby else _cardmax_offer
        return dict(source=source.value, product_url=url, currency='JPY', **parse(page, url, game))
    except httpx.TimeoutException:
        raise ProductSourceError('product_timeout') from None
    except httpx.HTTPError:
        raise ProductSourceError('product_unavailable') from None
    except ValueError:
        raise ProductSourceError('product_unreadable') from None
    finally:
        client.close()
