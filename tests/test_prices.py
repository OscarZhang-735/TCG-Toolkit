from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

from tcg_toolkit.catalog import prices
from tcg_toolkit.catalog.errors import SearchRequestError
from tcg_toolkit.catalog.models import Game, SearchItem, SearchPage, StockStatus
from tcg_toolkit.catalog.providers.cardmax import CardMaxSearcher
from tcg_toolkit.catalog.search import SEARCHERS
from test_cards import client
from test_product_details import FIXTURE, offer_html

PARAMS = {'merchant': 'yuyutei', 'game': 'ws', 'card_number': 'ABC-001'}


def card(**fields):
    item = SearchItem(Game.WS, 'Card', 'ABC-001', 100, 'JPY', StockStatus.IN_STOCK,
                      2, '2', 'https://yuyu-tei.jp/sell/ws/card/set/001', rarity='SR', condition='normal')
    return replace(item, **fields)


def source(monkeypatch, pages, game=Game.WS):
    class Fake:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_pages(self, number):
            for index, page in enumerate(pages, 1):
                if isinstance(page, Exception):
                    raise page
                yield SearchPage(page, number, index, index + 1 if index < len(pages) else None)

    monkeypatch.setitem(SEARCHERS, game, Fake)


def test_exact_matches_all_pages_conditions_and_deduplication(client, monkeypatch):
    first = card(card_number=' abc-001 ')
    second = card(product_url=first.product_url + '2', rarity='R', price=0,
                  stock_quantity=0, stock_status=StockStatus.OUT_OF_STOCK)
    source(monkeypatch, [[card(card_number='ABC-001A'), card(condition='damaged'), first],
                         [replace(first, product_url=first.product_url + '/'), second, card(card_number='XABC-001')]])
    monkeypatch.setattr(prices, 'fetch_product_snapshot', lambda *args: pytest.fail('explicit condition needs no detail'))
    response = client.get('/api/prices', params=PARAMS)
    assert response.status_code == 200
    assert response.json() == {**PARAMS, 'items': [
        {**{key: item.to_dict()[key] for key in prices.OFFER_FIELDS}, 'condition': 'normal'}
        for item in (first, second)]}


@pytest.mark.parametrize('game,shop_number,official', [
    ('ws', 'WS-W125-070S', 'BD/W125-070S'), ('wsr', 'WR-R13-001', 'OS13/R13-001')])
def test_hobby_official_price_requires_unique_printing(client, monkeypatch, game, shop_number, official):
    catalog = [{'card_number': official}]
    monkeypatch.setattr(prices, 'ws_catalog_search', lambda *args: (catalog, len(catalog)))
    def get(self, url, *, timeout):
        html = (Path(__file__).parent / 'fixtures/hobbystation_lo.html').read_text(encoding='utf-8')
        html = html.replace('LO-LO-4899', shop_number).replace('/lo/', '/ws/' if game == 'ws' else '/wr/')
        html = html.replace('アストラルの研究/式部 茉優', 'カード(SR)')
        return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr('tcg_toolkit.catalog.providers.hobbystation.HobbyStationClient.get', get)
    params = dict(merchant='hobbystation', game=game, card_number=official)
    [offer] = client.get('/api/prices', params=params).json()['items']
    assert offer['card_number'] == official and offer['price'] == 80
    catalog.append({'card_number': 'OTHER/' + official.split('/')[1]})
    assert client.get('/api/prices', params=params).json()['items'] == []


def test_cardmax_quotes_read_stock_for_only_the_exact_nm_card(client, monkeypatch):
    html = (Path(__file__).parent / 'fixtures/cardmax_sev.html').read_text(encoding='utf-8')
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.select('.M_pager li.next'):
        link.decompose()
    calls = []
    def respond(request):
        calls.append(request.url.path)
        body = str(soup) if len(calls) == 1 else '<span class="M_item-stock-small">残りあと4個</span>'
        return httpx.Response(200, text=body)
    monkeypatch.setattr(CardMaxSearcher, 'client_type', staticmethod(lambda: httpx.Client(
        transport=httpx.MockTransport(respond))))
    [offer] = client.get('/api/prices', params=dict(merchant='cardmax', game='sev', card_number='BP22-T03')).json()['items']
    assert offer['stock_quantity'] == 4 and offer['condition'] == 'normal'
    assert calls == ['/shop/shopbrand.html', httpx.URL(offer['product_url']).path]


def test_later_page_failure_does_not_return_partial_quotes(client, monkeypatch):
    error = SearchRequestError(
        'private upstream detail', game=Game.WS, keyword='ABC-001', page=2, url='internal')
    source(monkeypatch, [[card()], error])
    response = client.get('/api/prices', params=PARAMS)
    assert response.status_code == 502
    assert response.json() == {'detail': 'price_source_unavailable'}


@pytest.mark.parametrize('mismatch', [False, True])
def test_unknown_condition_fetches_normal_detail(client, monkeypatch, mismatch):
    number = 'OS01/R01-053SEC'
    url = 'https://yuyu-tei.jp/sell/wsr/card/OS01/10240'
    source(monkeypatch, [[card(game=Game.WSR, card_number=number, product_url=url, condition=None)]], Game.WSR)
    calls = []
    html = FIXTURE + offer_html('0', '0') + offer_html('580', '2', kizu=1)
    if mismatch:
        html = html.replace(number, 'OTHER')
    def get(self, requested, *, timeout):
        calls.append(requested)
        return httpx.Response(200, text=html, request=httpx.Request('GET', requested))
    monkeypatch.setattr('tcg_toolkit.catalog.providers.yuyutei_products.SearchProxyClient.get', get)
    response = client.get('/api/prices', params={**PARAMS, 'game': 'wsr', 'card_number': number})
    assert calls == [url]
    if mismatch:
        assert response.status_code == 502 and response.json()['detail'] == 'price_source_unreadable'
    else:
        assert response.status_code == 200
        [item] = response.json()['items']
        assert (item['price'], item['currency'], item['condition'], item['stock_status']) == (0, 'JPY', 'normal', 'out_of_stock')
