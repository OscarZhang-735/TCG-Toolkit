from pathlib import Path

import httpx
import pytest

from tcg_toolkit import CardMaxSEVSearcher, CardMaxWSSearcher, SearchParseError, create_searcher


def fixture(game):
    return (Path(__file__).parent / 'fixtures' / f'cardmax_{game}.html').read_text(encoding='utf-8')


def test_ws_number_query_and_extended_japanese():
    def respond(request):
        assert request.url.host == 'www.cardmax.jp'
        assert request.url.path == '/shop/shopbrand.html'
        assert dict(request.url.params) == {'search': 'AMG W121-019SP', 'category': 'ct1847'}
        html = fixture('ws').replace('夜重', '夜重№1')
        return httpx.Response(200, content=html.encode('euc_jis_2004'),
                              headers={'content-type': 'text/html; charset=EUC-JP'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        page = CardMaxWSSearcher(client=client).search_page('AMG/W121-019SP')
    assert page.keyword == 'AMG/W121-019SP' and page.next_page is None
    item, = page.items
    assert (item.name, item.card_number, item.price, item.rarity) == (
        'サイン付)本物の才能 夜重№1', 'AMG/W121-019SP', 4980, 'SP')
    assert item.product_url == 'https://www.cardmax.jp/shopdetail/000000179635'
    assert item.image_url.endswith('/0000001796353_cQyYtxx.jpg')
    assert (item.stock_status, item.stock_quantity, item.cart) == ('unknown', None, None)


def test_only_explicit_zero_count_is_empty():
    with CardMaxWSSearcher() as searcher:
        args = dict(keyword='x', page=1, url=searcher.base_url)
        empty = '<form name="form_se"><ul class="search-change"><li class="left">総0件</li></ul></form>'
        assert searcher.parse_page(empty, **args).items == []
        with pytest.raises(SearchParseError):
            searcher.parse_page(empty.replace('総0件', '総1件'), **args)
        with pytest.raises(SearchParseError, match='Item 1'):
            searcher.parse_page(fixture('ws').replace('4,980円', '未定'), **args)


@pytest.mark.parametrize(('stock', 'expected'), [
    (False, [('unknown', None), ('unknown', None)]),
    (True, [('in_stock', 4), ('out_of_stock', 0)]),
])
def test_optional_detail_stock(stock, expected, monkeypatch):
    paths = []
    def respond(request):
        paths.append(request.url.path)
        if request.url.path == '/shop/shopbrand.html':
            return httpx.Response(200, text=fixture('sev'))
        labels = {
            '/shopdetail/000000235105': '<span class="M_item-stock-smallstock">残りあと4個</span>',
            '/shopdetail/000000235103': '<span class="M_item-stock-soldout">売り切れ</span>',
        }
        return httpx.Response(200, content=labels[request.url.path].encode('euc-jp'),
                              headers={'content-type': 'text/html; charset=EUC-JP'})
    monkeypatch.setattr(CardMaxSEVSearcher, 'client_type', staticmethod(
        lambda: httpx.Client(transport=httpx.MockTransport(respond))))
    with create_searcher('cardmax', 'sev', stock=stock) as searcher:
        result = searcher.search_page('BP22')
    assert result.to_dict()['source'] == 'cardmax'
    assert [(i.stock_status, i.stock_quantity) for i in result.items] == expected
    assert result.next_page == 2
    assert len(paths) == (3 if stock else 1)
