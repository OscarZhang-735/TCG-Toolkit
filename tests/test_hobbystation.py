from pathlib import Path

import httpx
import pytest

from tcg_toolkit import HobbyStationLyceeSearcher, SearchPaginationError, SearchParseError


def fixture(name):
    return (Path(__file__).parent / 'fixtures' / f'hobbystation_lo{name}.html').read_text(encoding='utf-8')


def test_hobby_page_fields():
    with HobbyStationLyceeSearcher() as searcher:
        result = searcher.parse_page(fixture(''), keyword='LO-4899', page=1,
                                     url=searcher.base_url + searcher.search_path)
    assert result.next_page is None
    normal, parallel = result.items
    assert (normal.card_number, normal.name, normal.price) == ('LO-4899', 'アストラルの研究/式部 茉優', 80)
    assert (parallel.card_number, parallel.price) == ('LO-4899-K', 2580)
    assert normal.product_url == 'https://www.hobbystation-single.jp/lo/product/detail/305273'
    assert normal.image_url.endswith('/lo/LO-LO-4899.jpg')
    assert (normal.stock_status, normal.stock_quantity, normal.cart, normal.rarity) == ('in_stock', None, None, None)


def test_hobby_pagination_and_stock(monkeypatch):
    def unavailable(*args, **kwargs):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(httpx, 'get', unavailable)
    def respond(request):
        assert request.url.path == '/lo/product/list'
        assert dict(request.url.params) == {'search_word': 'LO- &', 'match_type': '1', 'pageno': '2'}
        return httpx.Response(200, text=fixture('_2'))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = HobbyStationLyceeSearcher(client=client).search_page(' LO- & ', page=2)
    assert result.next_page == 3
    stocked, sold = result.items
    assert (stocked.stock_status, stocked.stock_quantity, stocked.rarity) == ('in_stock', 3, 'P')
    assert (sold.stock_status, sold.stock_quantity, sold.stock_text) == ('out_of_stock', 0, 'SOLD OUT')
    with HobbyStationLyceeSearcher() as searcher, pytest.raises(SearchPaginationError):
        searcher.parse_page(fixture('_2'), keyword='LO-', page=1, url=searcher.base_url)


def test_hobby_empty_and_unrecognized():
    with HobbyStationLyceeSearcher() as searcher:
        args = dict(keyword='x', page=1, url=searcher.base_url)
        empty = '<main><div class="ec-searchnavRole__counter">お探しの商品は見つかりませんでした</div></main>'
        assert searcher.parse_page(empty, **args).items == []
        with pytest.raises(SearchParseError):
            searcher.parse_page('<main>temporarily unavailable</main>', **args)
        with pytest.raises(SearchParseError, match='Item 1'):
            searcher.parse_page(fixture('').replace('80円', '未定'), **args)
