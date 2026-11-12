from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi import HTTPException

from tcg_toolkit import prices
from tcg_toolkit.errors import SearchParseError, SearchRequestError
from tcg_toolkit.models import Game, SearchItem, SearchPage, StockStatus
from tcg_toolkit.web import SEARCHERS
from test_cards import client
from test_product_details import FIXTURE, offer_html

PARAMS = {'merchant': 'yuyutei', 'game': 'ws', 'card_number': 'ABC-001'}


def card(**fields):
    item = SearchItem(Game.WS, 'Card', 'ABC-001', 100, 'JPY', StockStatus.IN_STOCK,
                      2, '2', 'https://yuyu-tei.jp/sell/ws/card/set/001', rarity='SR', condition='normal')
    return replace(item, **fields)


def source(monkeypatch, pages, game=Game.WS):
    calls = []

    class Fake:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_pages(self, number):
            calls.append(number)
            for index, page in enumerate(pages, 1):
                if isinstance(page, Exception):
                    raise page
                yield SearchPage(page, number, index, index + 1 if index < len(pages) else None)

    monkeypatch.setitem(SEARCHERS, game, Fake)
    return calls


def test_merchants_and_authentication(client):
    response = client.get('/api/prices/merchants')
    assert response.json() == {'items': [{'id': 'yuyutei', 'name': '游游亭',
                                         'games': ['ws', 'wsr', 'pm', 'sev', 'lycee', 'chaos']}]}
    assert response.headers['cache-control'] == 'no-store'
    client.headers.pop('Authorization')
    for path in ['/api/prices', '/api/prices/merchants']:
        response = client.get(path, params=PARAMS)
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('alias,game', [('WS', 'ws'), (' wSr ', 'wsr'), ('PM', 'pm'),
                                      ('SEV', 'sev'), ('Lycee', 'lycee'), ('ChaosTCG', 'chaos'), ('chaos', 'chaos')])
def test_games_aliases_and_trimmed_number(client, monkeypatch, alias, game):
    calls = source(monkeypatch, [[]], Game(game))
    response = client.get('/api/prices', params={**PARAMS, 'game': alias, 'card_number': ' ABC-001 '})
    assert response.status_code == 200
    assert response.json() == {**PARAMS, 'game': game, 'items': []}
    assert calls == ['ABC-001']
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('overrides,detail', [
    ({'merchant': 'other'}, 'unsupported_merchant'),
    *[({'game': game}, 'unsupported_merchant_game') for game in ('ws_en', 'WS EN', 'sev_en', 'SEV EN', 'custom', '')],
    *[({'card_number': value}, None) for value in ('', '  ', 'x' * 101)],
])
def test_reject_before_upstream(client, monkeypatch, overrides, detail):
    calls = source(monkeypatch, [[]])
    response = client.get('/api/prices', params={**PARAMS, **overrides})
    assert response.status_code == 422 and calls == []
    assert response.headers['cache-control'] == 'no-store'
    if detail:
        assert response.json()['detail'] == detail


@pytest.mark.parametrize('missing', list(PARAMS))
def test_required_parameters(client, missing):
    assert client.get('/api/prices', params={key: value for key, value in PARAMS.items() if key != missing}).status_code == 422


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


@pytest.mark.parametrize('kind,status,detail', [
    ('request', 502, 'price_source_unavailable'), ('timeout', 504, 'price_source_timeout'),
    ('parse', 502, 'price_source_unreadable'),
])
def test_later_page_failure_does_not_return_partial_quotes(client, monkeypatch, kind, status, detail):
    error = (SearchParseError if kind == 'parse' else SearchRequestError)(
        'private upstream detail', game=Game.WS, keyword='ABC-001', page=2, url='internal')
    if kind == 'timeout':
        error.__cause__ = httpx.ReadTimeout('secret')
    source(monkeypatch, [[card()], error])
    response = client.get('/api/prices', params=PARAMS)
    assert response.status_code == status
    assert response.json() == {'detail': detail}


@pytest.mark.parametrize('detail,status,expected', [
    ('product_timeout', 504, 'price_source_timeout'),
    ('product_unavailable', 502, 'price_source_unavailable'),
    ('product_unreadable', 502, 'price_source_unreadable'),
])
def test_detail_failures_use_price_errors(client, monkeypatch, detail, status, expected):
    source(monkeypatch, [[card(), card(condition=None, product_url=card().product_url + '2')]])
    def fail(*args):
        raise HTTPException(status, detail=detail)
    monkeypatch.setattr(prices, 'fetch_product_snapshot', fail)
    response = client.get('/api/prices', params=PARAMS)
    assert response.status_code == status
    assert response.json() == {'detail': expected}


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
    monkeypatch.setattr('tcg_toolkit.product_details.SearchProxyClient.get', get)
    response = client.get('/api/prices', params={**PARAMS, 'game': 'wsr', 'card_number': number})
    assert calls == [url]
    if mismatch:
        assert response.status_code == 502 and response.json()['detail'] == 'price_source_unreadable'
    else:
        assert response.status_code == 200
        [item] = response.json()['items']
        assert (item['price'], item['currency'], item['condition'], item['stock_status']) == (0, 'JPY', 'normal', 'out_of_stock')


@pytest.mark.parametrize('game', list(Game))
def test_real_search_html_through_lookup(client, monkeypatch, game):
    filename = 'lo' if game == Game.LYCEE else game.value
    html = (Path(__file__).parent / 'fixtures' / f'{filename}_1.html').read_text(encoding='utf-8')
    soup = BeautifulSoup(html, 'html.parser')
    for pager in soup.select('.pagination'):
        pager.decompose()
    first = soup.select_one('.card-product')
    number = first.select_one('span.border').get_text(strip=True)
    calls = []
    def get(self, url, *, timeout):
        calls.append(url)
        return httpx.Response(200, text=str(soup), request=httpx.Request('GET', url))
    monkeypatch.setattr('tcg_toolkit.external.search_proxy.SearchProxyClient.get', get)
    response = client.get('/api/prices', params={**PARAMS, 'game': game.value, 'card_number': number})
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert response.json()['items'][0]['card_number'] == number
    assert response.json()['items'][0]['currency'] == 'JPY'


@pytest.mark.parametrize('value,expected', [('0', 'normal'), ('1', 'damaged'), (None, None), ('?', None)])
def test_explicit_condition_does_not_change_search_response(value, expected):
    html = (Path(__file__).parent / 'fixtures/wsr_1.html').read_text(encoding='utf-8')
    soup = BeautifulSoup(html, 'html.parser')
    node = soup.select_one('.card-product')
    marker = node.select_one('input.cart_kizu')
    if value is None:
        marker.decompose()
    else:
        marker['value'] = value
    with SEARCHERS[Game.WSR]() as searcher:
        item = searcher.parse_item(node, url='https://yuyu-tei.jp/sell/wsr/s/search')
    assert item.condition == expected
    assert 'condition' not in item.to_dict()
    if value is None:
        assert item.cart['kizu'] == 0  # Existing cart API behavior is preserved.
