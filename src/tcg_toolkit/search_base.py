"""Shared fetching, pagination and parsing for merchant searchers."""

import time
from collections.abc import Iterator

import httpx
from bs4 import BeautifulSoup, Tag

from .errors import SearchPaginationError, SearchParseError, SearchRequestError
from .external.search_proxy import SearchProxyClient
from .models import Game, SearchItem, SearchPage, SearchSource


def _text(node: Tag | None) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


class PagedSearcher:
    """Sequential keyword search. Injected clients remain owned by the caller."""

    base_url: str
    game: Game
    source: SearchSource
    search_path: str
    cards_selector: str
    main_selector = "main"
    keyword_param = "search_word"
    page_param = "page"
    fixed_params: dict[str, str] = {}
    client_type = SearchProxyClient

    def __init__(self, *, client: httpx.Client | SearchProxyClient | None = None,
                 timeout: float = 30.0, page_interval: float = 1.0, stock: bool = False):
        if timeout <= 0 or page_interval < 0:
            raise ValueError("timeout must be positive and page_interval non-negative")
        self.timeout = timeout
        self.page_interval = page_interval
        self.stock = stock
        self._owns_client = client is None
        self._client = client if client is not None else self.client_type()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search_page(self, keyword: str, *, page: int = 1) -> SearchPage:
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("keyword must be a non-empty string")
        if type(page) is not int or page < 1:
            raise ValueError("page must be a positive integer")
        keyword = keyword.strip()
        params = {self.keyword_param: self.query_keyword(keyword), **self.fixed_params}
        if page != 1:
            params[self.page_param] = str(page)
        url = str(httpx.URL(self.base_url + self.search_path, params=params))
        html = self.fetch_html(url, keyword=keyword, page=page)
        return self.parse_page(html, keyword=keyword, page=page, url=url)

    def fetch_html(self, url: str, *, keyword: str, page: int) -> str:
        try:
            response = self._client.get(url, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchRequestError(str(exc), game=self.game, keyword=keyword, page=page, url=url) from exc
        return self.response_html(response)

    def query_keyword(self, keyword: str) -> str:
        return keyword

    def response_html(self, response: httpx.Response) -> str:
        return response.text

    def iter_pages(self, keyword: str, *, start_page: int = 1) -> Iterator[SearchPage]:
        result = self.search_page(keyword, page=start_page)
        while True:
            yield result
            if result.next_page is None:
                return
            time.sleep(self.page_interval)
            result = self.search_page(keyword, page=result.next_page)

    def iter_results(self, keyword: str, *, start_page: int = 1) -> Iterator[SearchItem]:
        for result in self.iter_pages(keyword, start_page=start_page):
            yield from result.items

    def parse_page(self, html: str, *, keyword: str, page: int, url: str) -> SearchPage:
        context = dict(game=self.game, keyword=keyword, page=page, url=url)
        soup = BeautifulSoup(html, "html.parser")
        main = soup.select_one(self.main_selector)
        cards = main.select(self.cards_selector) if main else []
        if not main or (not cards and not self.is_empty(main)):
            raise SearchParseError("Unrecognized search result page", **context)
        try:
            next_page = self.parse_pagination(main, page=page)
        except ValueError as exc:
            raise SearchPaginationError(str(exc), **context) from exc
        items = []
        for index, card in enumerate(cards):
            try:
                item = self.parse_item(card, url=url)
                if item is not None:
                    items.append(item)
            except ValueError as exc:
                raise SearchParseError(f"Item {index + 1}: {exc}", **context) from exc
        return SearchPage(items=items, keyword=keyword, page=page, next_page=next_page, source=self.source)

    def parse_item(self, card: Tag, *, url: str) -> SearchItem | None:
        raise NotImplementedError

    def is_empty(self, main: Tag) -> bool:
        raise NotImplementedError

    def parse_pagination(self, main: Tag, *, page: int) -> int | None:
        raise NotImplementedError
