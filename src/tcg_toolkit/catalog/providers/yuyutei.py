"""YuYu-Tei keyword search and game-specific storefront paths."""
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import Tag

from ..models import Game, SearchItem, SearchSource, StockStatus
from ..pagination import PagedSearcher, _text


class BaseSearcher(PagedSearcher):
    """Base class for the YuYu-Tei game searchers."""

    source = SearchSource.YUYUTEI
    base_url = "https://yuyu-tei.jp"
    cards_selector = ".cards-list .card-product"

    def is_empty(self, main: Tag) -> bool:
        return "検索条件に該当する商品は見つかりませんでした。" in main.get_text()

    def parse_item(self, card: Tag, *, url: str) -> SearchItem:
        name = _text(card.select_one("h4"))
        number = _text(card.select_one("span.border"))
        price_text = _text(card.select_one("strong"))
        price_match = re.fullmatch(r"([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*円", price_text)
        if not name or not number or not price_match:
            raise ValueError("Missing or invalid name, card number, or price")
        link = card.select_one(f'a[href*="/sell/{self.search_path.split("/")[2]}/card/"]')
        if link is None:
            raise ValueError("Missing product URL")
        stock_text = _text(card.select_one(".cart_sell_zaiko"))
        stock_status, stock_quantity = self.parse_stock(stock_text)
        active = card.select_one('input.cart_active')
        active_value = str(active.get('value', '')).strip() if active else ''
        if re.fullmatch(r'[0-9]+', active_value):
            stock_quantity = int(active_value)
            stock_status = StockStatus.IN_STOCK if stock_quantity else StockStatus.OUT_OF_STOCK
        image = card.select_one('.product-img img')
        image_url = None
        if image is not None:
            source = str(image.get('src', '')).strip()
            if source:
                candidate = urljoin(url, source)
                if urlsplit(candidate).scheme in ('http', 'https'):
                    image_url = candidate
        return SearchItem(
            game=self.game, name=name, card_number=number,
            price=int(price_match[1].replace(",", "")), currency="JPY",
            stock_status=stock_status, stock_quantity=stock_quantity,
            stock_text=stock_text, product_url=urljoin(url, link["href"]),
            image_url=image_url,
            rarity=self.parse_rarity(card, number),
            cart=self.parse_cart_target(card),
            condition=self.parse_condition(card),
        )

    def parse_condition(self, card: Tag) -> str | None:
        nodes = card.select('input.cart_kizu')
        if len(nodes) != 1:
            return None
        return {'0': 'normal', '1': 'damaged'}.get(str(nodes[0].get('value', '')).strip())

    def parse_cart_target(self, card: Tag) -> dict | None:
        def value(name):
            node = card.select_one(f'input.cart_{name}')
            return str(node.get('value', '')).strip() if node else ''
        gid, ver, cid, kizu = value('gid'), value('ver'), value('cid'), value('kizu') or '0'
        # Same constraints as the cart API; anything else simply disables add-to-cart.
        if (re.fullmatch(r'[0-9]+', gid) and re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}', ver)
                and re.fullmatch(r'[0-9]{1,32}', cid) and kizu in ('0', '1')):
            return {'gid': int(gid), 'ver': ver, 'cid': cid, 'kizu': int(kizu)}
        return None

    def parse_rarity(self, card: Tag, number: str) -> str | None:
        group = card.find_parent(class_='cards-list')
        label = _text(group.select_one('h3 span')) if group else ''
        if label and label not in ('-', '－', '—'):
            return label
        image = card.select_one('.product-img img')
        alt = str(image.get('alt', '')).strip() if image else ''
        if alt.startswith(number + ' '):
            token = alt[len(number):].strip().split(maxsplit=1)[0:1]
            if token and re.fullmatch(r'[A-Za-z0-9+★☆/-]+', token[0]):
                return token[0]
        return None

    def parse_stock(self, text: str) -> tuple[StockStatus, int | None]:
        value = re.sub(r"^在庫\s*[:：]\s*", "", text).strip()
        quantity = re.fullmatch(r"([0-9]+)\s*点", value)
        if quantity:
            count = int(quantity[1])
            return (StockStatus.IN_STOCK if count else StockStatus.OUT_OF_STOCK), count
        if value == "×":
            return StockStatus.OUT_OF_STOCK, 0
        if value == "◯":
            return StockStatus.IN_STOCK, None
        return StockStatus.UNKNOWN, None

    def parse_pagination(self, main: Tag, *, page: int) -> int | None:
        pagers = main.select(".pagination")
        if not pagers:
            if page != 1:
                raise ValueError("No pagination confirms the requested page")
            return None
        next_pages = []
        for pager in pagers:
            active = pager.select_one('[aria-current="page"], .page-item.active')
            if not _text(active).isdigit() or int(_text(active)) != page:
                raise ValueError("Active page does not match the requested page")
            link = pager.select_one('a[aria-label="Next"]')
            if link is None:
                raise ValueError("Missing Next pagination control")
            href = str(link.get("href", ""))
            disabled = ("disabled" in link.get("class", [])
                        or link.get("aria-disabled") == "true"
                        or link.find_parent(class_="disabled") is not None)
            if disabled or href.startswith("javascript:"):
                next_pages.append(None)
                continue
            values = parse_qs(urlsplit(href).query).get("page", [])
            if len(values) != 1 or not values[0].isdigit() or int(values[0]) <= page:
                raise ValueError("Next page must be a greater positive page number")
            next_pages.append(int(values[0]))
        if any(value != next_pages[0] for value in next_pages):
            raise ValueError("Pagination controls disagree")
        return next_pages[0]


class WSRSearcher(BaseSearcher):
    game = Game.WSR
    search_path = "/sell/wsr/s/search"


class LyceeSearcher(BaseSearcher):
    game = Game.LYCEE
    search_path = "/sell/lo/s/search"


class ChaosTCGSearcher(BaseSearcher):
    game = Game.CHAOS
    search_path = "/sell/chaos/s/search"


class WSSearcher(BaseSearcher):
    game = Game.WS
    search_path = "/sell/ws/s/search"


class PMSearcher(BaseSearcher):
    game = Game.PM
    search_path = "/sell/pm/s/search"


class SEVSearcher(BaseSearcher):
    game = Game.SEV
    search_path = "/sell/sev/s/search"


SEARCHERS = {searcher.game: searcher for searcher in (
    WSRSearcher, LyceeSearcher, ChaosTCGSearcher, WSSearcher, PMSearcher, SEVSearcher,
)}
