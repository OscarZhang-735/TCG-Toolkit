from pathlib import Path

import httpx
import pytest

from tcg_toolkit import HobbyStationLyceeSearcher, SearchPaginationError, SearchParseError
from tcg_toolkit.catalog.providers.hobbystation import HobbyStationClient


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


def test_hobby_rarity_matches_exact_lycee_printing(monkeypatch):
    official = (Path(__file__).parent / 'fixtures' / 'official_lycee.html').read_text(encoding='utf-8')
    official = (official.replace('LO-1606-S', 'LO-4899-K').replace('LO-1606', 'LO-4899')
                .replace('>SP<', '>KR<').replace('>SR<', '>R<'))
    def lookup(url, **kwargs):
        assert kwargs['params']['word'] in ('LO-4899', 'LO-4899-K')
        return httpx.Response(200, text=official, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', lookup)
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=fixture('')))) as client:
        normal, parallel = HobbyStationLyceeSearcher(client=client).search_page('LO-4899').items
    assert (normal.rarity, parallel.rarity) == ('R', 'KR')


def test_hobby_fullwidth_rarity_before_condition():
    html = fixture('').replace('アストラルの研究/式部 茉優', 'カード（ＳＲ）（キズあり）')
    with HobbyStationLyceeSearcher() as searcher:
        result = searcher.parse_page(html, keyword='x', page=1, url=searcher.base_url)
    assert all(item.rarity == 'SR' for item in result.items)


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


def test_hobby_landing_page_reloads_once(monkeypatch):
    monkeypatch.setenv('TCG_SEARCH_PROXY_URL', '')
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, text='<title>安全な接続を確認中...</title>'
                              '<script>document.cookie = "eccube_bot_check=sample; path=/";</script>')

    client = HobbyStationClient(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(SearchParseError):
            HobbyStationLyceeSearcher(client=client).search_page('LO-4899')
    finally:
        client.close()
    assert len(requests) == 2
    assert 'cookie' not in requests[0].headers
    assert requests[1].headers['cookie'] == 'eccube_bot_check=sample'
