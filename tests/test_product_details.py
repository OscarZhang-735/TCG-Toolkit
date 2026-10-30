from auth_helpers import register_user
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import tcg_toolkit.product_details as module
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'
FIXTURE = (Path(__file__).parent / 'fixtures' / 'product_wsr.html').read_text(encoding='utf-8')


class FakeClient:
    calls = []
    reply = None

    def get(self, url, *, timeout):
        FakeClient.calls.append(url)
        if isinstance(FakeClient.reply, Exception):
            raise FakeClient.reply
        return httpx.Response(200, text=FakeClient.reply, request=httpx.Request('GET', url))

    def close(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TCG_USER_DB', str(tmp_path / 'users.sqlite3'))
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    monkeypatch.setattr(module, 'SearchProxyClient', FakeClient)
    monkeypatch.setattr(module, '_cache', {})
    FakeClient.calls, FakeClient.reply = [], FIXTURE
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        response = register_user(client)
        client.headers['Authorization'] = 'Bearer ' + response.json()['token']
        yield client


def test_product_details_from_page_and_cached(client):
    for _ in range(2):
        response = client.get('/api/yuyutei/product', params={'gid': 56, 'ver': 'OS01', 'cid': '10240'})
        assert response.status_code == 200
        assert response.json() == {'gid': 56, 'ver': 'OS01', 'cid': '10240',
                                   'card_number': 'OS01/R01-053SEC', 'rarity': 'SEC'}
    assert FakeClient.calls == ['https://yuyu-tei.jp/sell/wsr/card/OS01/10240']
    assert response.headers['cache-control'] == 'no-store'


def test_rarity_is_optional_but_number_is_required():
    assert module.parse_product(FIXTURE.replace('SEC 大人', '大人'))['rarity'] is None
    with pytest.raises(ValueError):
        module.parse_product('<main>moved</main>')


def test_sev_product_details_and_stock(client):
    FakeClient.reply = '<div id="product-detail"><span class="border">BP01-U01</span></div>' \
        '<div id="power"><h3>UR Card</h3></div><div class="counter">' \
        '<input class="cart_kizu" value="0"><input class="cart_active" value="5"></div>'
    response = client.get('/api/yuyutei/product', params={
        'gid': 47, 'ver': 'bp01', 'cid': '10238', 'stock': 'true'})
    assert response.status_code == 200
    assert response.json()['card_number'] == 'BP01-U01'
    assert response.json()['rarity'] == 'UR'
    assert response.json()['stock_quantity'] == 5
    assert FakeClient.calls == ['https://yuyu-tei.jp/sell/sev/card/bp01/10238']


@pytest.mark.parametrize('params', [{'gid': 1, 'ver': 'OS01', 'cid': '1'}, {'gid': 56, 'ver': '../x', 'cid': '1'},
                                    {'gid': 56, 'ver': 'OS01', 'cid': '1/2'}, {'gid': 56, 'ver': 'OS01'}])
def test_invalid_identity_never_fetches(client, params):
    assert client.get('/api/yuyutei/product', params=params).status_code == 422
    assert FakeClient.calls == []


@pytest.mark.parametrize('reply,status', [(httpx.ReadTimeout('x'), 504), (httpx.ConnectError('x'), 502),
                                          ('<html>not a product</html>', 502)])
def test_upstream_failures_are_not_cached(client, reply, status):
    FakeClient.reply = reply
    params = {'gid': 56, 'ver': 'OS01', 'cid': '10240'}
    assert client.get('/api/yuyutei/product', params=params).status_code == status
    FakeClient.reply = FIXTURE
    assert client.get('/api/yuyutei/product', params=params).status_code == 200


def test_requires_sign_in(client):
    client.headers.pop('Authorization')
    assert client.get('/api/yuyutei/product', params={'gid': 56, 'ver': 'OS01', 'cid': '1'}).status_code == 401

def stock_html(value, kizu=0):
    return FIXTURE + f'<div class="counter"><input class="cart_kizu" value="{kizu}"><input class="cart_active" value="{value}"></div>'


def test_stock_reads_fresh_product_page_without_polluting_details_cache(client):
    params = {'gid': 56, 'ver': 'OS01', 'cid': '10240'}
    client.get('/api/yuyutei/product', params=params)
    for quantity in (25, 0, 3):
        FakeClient.reply = stock_html(quantity)
        response = client.get('/api/yuyutei/product', params={**params, 'stock': 'true'})
        assert response.status_code == 200
        assert response.json()['stock_quantity'] == quantity
        assert response.json()['stock_status'] == ('in_stock' if quantity else 'out_of_stock')
        assert response.headers['cache-control'] == 'no-store'
    assert len(FakeClient.calls) == 4
    assert 'stock_quantity' not in client.get('/api/yuyutei/product', params=params).json()
    assert len(FakeClient.calls) == 4


@pytest.mark.parametrize('value,expected', [('12', 12), ('0', 0), ('', None), ('-1', None), ('10+', None)])
def test_stock_parser_validates_hidden_quantity(value, expected):
    assert module.parse_product_stock(stock_html(value))['stock_quantity'] == expected


def test_stock_is_condition_specific_and_unknown_is_not_zero():
    assert module.parse_product_stock(stock_html(25), kizu=1)['stock_quantity'] is None
    assert module.parse_product_stock(stock_html(4, 1), kizu=1)['stock_quantity'] == 4
    assert module.parse_product_stock(FIXTURE)['stock_status'] == 'unknown'
    assert module.parse_product_stock(stock_html(25) + stock_html(10))['stock_quantity'] is None


def test_stock_failure_and_invalid_condition(client):
    FakeClient.reply = '<html>broken</html>'
    params = {'gid': 56, 'ver': 'OS01', 'cid': '1', 'stock': 'true'}
    assert client.get('/api/yuyutei/product', params=params).status_code == 502
    FakeClient.calls.clear()
    assert client.get('/api/yuyutei/product', params={**params, 'kizu': 2}).status_code == 422
    assert FakeClient.calls == []
