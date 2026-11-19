"""Card Max's category-scoped single-card search."""

import re
from dataclasses import replace
from functools import partial
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from ..identity import CARDMAX_CATEGORIES
from ..models import Game, SearchItem, SearchPage, SearchSource, StockStatus
from ..pagination import PagedSearcher, _text


def read_stock(soup: BeautifulSoup) -> tuple[StockStatus, int | None, str]:
    node = soup.select_one('span[class*="M_item-stock-"]')
    label = _text(node)
    count = re.fullmatch(r"残りあと\s*([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*個", label)
    quantity = None
    status = StockStatus.UNKNOWN
    if node and "M_item-stock-soldout" in node.get("class", []):
        status, quantity = StockStatus.OUT_OF_STOCK, 0
    elif count:
        quantity = int(count[1].replace(",", ""))
        status = StockStatus.IN_STOCK if quantity else StockStatus.OUT_OF_STOCK
    elif label in ("在庫あり", "在庫有り"):
        status = StockStatus.IN_STOCK
    return status, quantity, label


class CardMaxSearcher(PagedSearcher):
    source = SearchSource.CARDMAX
    base_url = "https://www.cardmax.jp"
    search_path = "/shop/shopbrand.html"
    keyword_param = "search"
    main_selector = 'form[name="form_se"]'
    cards_selector = ".search-end-list tr:has(td)"
    client_type = partial(httpx.Client, trust_env=False, follow_redirects=True)

    def search_page(self, keyword: str, *, page: int = 1) -> SearchPage:
        result = super().search_page(keyword, page=page)
        if not self.stock:
            return result
        items = [self.read_item_stock(item, keyword=result.keyword, page=page) for item in result.items]
        return replace(result, items=items)

    def read_item_stock(self, item: SearchItem, *, keyword: str, page: int) -> SearchItem:
        html = self.fetch_html(item.product_url, keyword=keyword, page=page)
        soup = BeautifulSoup(html, "html.parser")
        status, quantity, label = read_stock(soup)
        return replace(item, stock_quantity=quantity, stock_status=status, stock_text=label)

    def response_html(self, response: httpx.Response) -> str:
        # The EUC-JP storefront includes extended characters such as №.
        if response.encoding and response.encoding.lower() == "euc-jp":
            return response.content.decode("euc_jis_2004", errors="replace")
        return response.text

    def is_empty(self, main: Tag) -> bool:
        return _text(main.select_one(".search-change .left")) == "総0件"

    def parse_pagination(self, main: Tag, *, page: int) -> int | None:
        pager = main.select_one(".M_pager")
        if page == 1 and self.is_empty(main):
            return None
        if pager is None or _text(pager.select_one("li.active")) != str(page):
            raise ValueError("Active page does not match the requested page")
        # Both the previous and next arrows use li.next.
        for link in pager.select("li.next a"):
            if _text(link).startswith("次の"):
                values = parse_qs(urlsplit(link.get("href", "")).query).get("page")
                if values != [str(page + 1)]:
                    raise ValueError("Invalid next page")
                return page + 1
        return None

    def parse_item(self, card: Tag, *, url: str) -> SearchItem | None:
        cells = card.find_all("td", recursive=False)
        if len(cells) != 5:
            raise ValueError("Missing search result columns")
        link = cells[2].select_one("a[href]")
        title = _text(link)
        if re.search(r"BOX\s*【", title, flags=re.IGNORECASE):
            return None

        # The set precedes the name. The last parenthesized field before ◇
        # is the card number; the preceding field, when present, is the rarity.
        label = title.partition("◇")[0].strip()
        fields = re.fullmatch(r"(?:\(([^()]+)\)|【([^】]+)】)\s*(.+?)\s*\(([^()]*[0-9][^()]*)\)", label)
        if not fields:
            raise ValueError("Missing card name or number")
        series = (fields[1] or fields[2]).strip()
        name = fields[3].strip()
        rarity_match = re.search(r"\(([^()]+)\)$", name)
        rarity = _text(cells[3]) or (rarity_match[1] if rarity_match else None)
        if rarity_match:
            name = name[:rarity_match.start()].strip()
        number = fields[4].strip().replace("ー", "-")
        price = re.fullmatch(r"([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)円", _text(cells[4]))
        brandcode = parse_qs(urlsplit(link["href"]).query).get("brandcode", [])
        if not name or not price or len(brandcode) != 1 or not re.fullmatch(r"[0-9]+", brandcode[0]):
            raise ValueError("Invalid product name, price or link")
        image = cells[1].select_one("img[src]")
        return SearchItem(
            game=self.game, name=name, card_number=self.card_number(series, number),
            price=int(price[1].replace(",", "")), currency="JPY", rarity=rarity,
            product_url=f"{self.base_url}/shopdetail/{brandcode[0]}",
            image_url=urljoin(url, image["src"]) if image and image["src"] else None,
            stock_status=StockStatus.UNKNOWN, stock_quantity=None, stock_text="",
        )

    def card_number(self, series: str, number: str) -> str:
        raise NotImplementedError


class CardMaxWSSearcher(CardMaxSearcher):
    game = Game.WS
    fixed_params = {"category": CARDMAX_CATEGORIES[game]}

    def query_keyword(self, keyword: str) -> str:
        # Card Max indexes "(P3) ... (SE46-37PER)" as two search terms.
        if re.fullmatch(r"[a-zA-Z0-9]+/[a-zA-Z0-9]+-[a-zA-Z0-9-]+", keyword):
            return keyword.replace("/", " ")
        return keyword

    def card_number(self, series: str, number: str) -> str:
        if "/" in number or number.startswith(series + "-"):
            return number
        return f"{series}/{number}"


class CardMaxSEVSearcher(CardMaxSearcher):
    game = Game.SEV
    fixed_params = {"category": CARDMAX_CATEGORIES[game]}

    def card_number(self, series: str, number: str) -> str:
        return number if "-" in number else f"{series}-{number}"
