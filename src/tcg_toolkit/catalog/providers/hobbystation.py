"""Hobby Station keyword search."""
import re
import unicodedata
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import Tag

from ...external.search_proxy import SearchProxyClient
from ..identity import HOBBY_GAME_DIRS
from ..models import Game, SearchItem, SearchSource, StockStatus
from ..pagination import PagedSearcher, _text


def hobby_number(game, number):
    return re.sub(r'^LO-(?:D-)?(?=LO-)', '', number) if game == Game.LYCEE else number


def hobby_rarity(name):
    label = unicodedata.normalize('NFKC', name)
    rarity = re.search(r'\(\s*([A-Z][A-Z0-9+★☆]*)\s*\)(?:\s*\([^()]*[ぁ-龯][^()]*\))*$', label)
    return rarity[1] if rarity else None


def hobby_stock(detail):
    if detail.select_one('img[alt="SOLD OUT"]'):
        return 'SOLD OUT', StockStatus.OUT_OF_STOCK, 0
    label = _text(detail.select_one('.stock'))
    value = re.sub(r'^在庫数\s*[:：]\s*', '', label)
    if re.fullmatch(r'[0-9]+', value):
        quantity = int(value)
        return label, StockStatus.IN_STOCK if quantity else StockStatus.OUT_OF_STOCK, quantity
    return label, StockStatus.IN_STOCK if value in ('◎', '○', '◯', '△') else StockStatus.UNKNOWN, None


class HobbyStationClient(SearchProxyClient):
    relay_host = 'www.hobbystation-single.jp'

    def get(self, url, *, timeout):
        response = super().get(url, timeout=timeout)
        if (response.status_code == 200 and response.url.host == self.relay_host
                and '<title>安全な接続を確認中...</title>' in response.text):
            # The storefront issues a session cookie in its landing page, then reloads.
            cookie = re.search(r'document\.cookie\s*=\s*"eccube_bot_check=([\w-]+);', response.text)
            if cookie:
                self.client.cookies.set('eccube_bot_check', cookie[1], domain=self.relay_host, path='/')
                return super().get(url, timeout=timeout)
        return response


class HobbyStationSearcher(PagedSearcher):
    source = SearchSource.HOBBYSTATION
    base_url = f"https://{HobbyStationClient.relay_host}"
    cards_selector = "section.result .searchRsultList > li"
    page_param = "pageno"
    fixed_params = {"match_type": "1"}
    client_type = HobbyStationClient

    def is_empty(self, main: Tag) -> bool:
        return _text(main.select_one('.ec-searchnavRole__counter')) == 'お探しの商品は見つかりませんでした'

    def parse_item(self, card: Tag, *, url: str) -> SearchItem:
        link = card.select_one('.list_product_Name_pc a[href]')
        name = _text(link)
        number = _text(card.find('div', recursive=False))
        detail = card.select_one('.packageDetail')
        price = re.match(r'([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*円', _text(detail))
        if not name or not number or not price or not link.get('href'):
            raise ValueError("Missing or invalid name, card number, price or product URL")
        stock_text, status, quantity = hobby_stock(detail)
        image = card.select_one('figure img[src]')
        return SearchItem(
            game=self.game, name=name, card_number=hobby_number(self.game, number),
            price=int(price[1].replace(',', '')), currency='JPY',
            stock_status=status, stock_quantity=quantity, stock_text=stock_text,
            product_url=urljoin(url, link['href']),
            image_url=urljoin(url, image['src']) if image and image['src'] else None,
            rarity=hobby_rarity(name),
            condition='damaged' if re.search(r'(?:キズ|傷)(?:あり|有)', name) or number.startswith(('LO-D-', 'WS-D-', 'WR-D-')) else 'normal',
        )

    def parse_pagination(self, main: Tag, *, page: int) -> int | None:
        pager = main.select_one('.ec-pager')
        if pager is None:
            if page != 1:
                raise ValueError("No pagination confirms the requested page")
            return None
        if _text(pager.select_one('.ec-pager__item--active')) != str(page):
            raise ValueError("Active page does not match the requested page")
        for link in pager.select('a'):
            if _text(link) == '次へ':
                values = parse_qs(urlsplit(link.get('href', '')).query).get('pageno')
                if values != [str(page + 1)]:
                    raise ValueError("Invalid next page")
                return page + 1
        return None


class HobbyStationLyceeSearcher(HobbyStationSearcher):
    game = Game.LYCEE
    search_path = f"/{HOBBY_GAME_DIRS[game]}/product/list"

class HobbyStationWSSearcher(HobbyStationSearcher):
    game = Game.WS
    search_path = f"/{HOBBY_GAME_DIRS[game]}/product/list"


class HobbyStationWSRSearcher(HobbyStationSearcher):
    game = Game.WSR
    search_path = f"/{HOBBY_GAME_DIRS[game]}/product/list"
