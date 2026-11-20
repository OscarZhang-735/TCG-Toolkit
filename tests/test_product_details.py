from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import tcg_toolkit.catalog.providers.yuyutei_products as module
from auth_helpers import register_user
from tcg_toolkit.catalog.providers import storefront_details
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'
FIXTURE = (Path(__file__).parent / 'fixtures' / 'product_wsr.html').read_text(encoding='utf-8')


class FakeClient:
    calls = []
    reply = None

    def get(self, url, *, timeout):
        FakeClient.calls.append(url)
        return httpx.Response(200, text=FakeClient.reply, request=httpx.Request('GET', url))

    def close(self):
        pass


@pytest.fixture
def client(postgres_schema, monkeypatch):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    monkeypatch.setattr(module, 'SearchProxyClient', FakeClient)
    monkeypatch.setattr(module, '_cache', {})
    FakeClient.calls, FakeClient.reply = [], FIXTURE
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        response = register_user(client)
        client.headers['Authorization'] = 'Bearer ' + response.json()['token']
        yield client


@pytest.mark.parametrize('source,url,html,expected', [
    ('hobbystation', 'https://www.hobbystation-single.jp/lo/product/detail/305273', '''
        <div class="detailWrap"><div class="detail"><div>LO-LO-4899</div><div class="itemName"><h2>カード(SR)</h2></div>
        <dl class="price"><dd class="stock">在庫数：◎</dd></dl></div></div>''', ('in_stock', None, 'lycee', 'LO-4899', 'SR')),
    ('cardmax', 'https://www.cardmax.jp/shopdetail/000000160917', '''
        <div class="product-info"><h2>(GCR) カード(PR)(BD/W125-P05S)◇PR【ヴァイスシュヴァルツ】 NM</h2></div>
        <div id="M_itemInfo"><span class="M_item-stock-small">残りあと5個</span></div>''',
        ('in_stock', 5, 'ws', 'BD/W125-P05S', 'PR')),
])
def test_merchant_stock_reads_the_cart_url_without_credentials_or_game(client, monkeypatch, source, url, html, expected):
    monkeypatch.setattr(storefront_details, 'HobbyStationClient', FakeClient)
    monkeypatch.setattr(storefront_details.CardMaxSearcher, 'client_type', FakeClient)
    FakeClient.reply = f'<link rel="canonical" href="{url}">' + html
    path = f'/api/{source}/product/stock'
    response = client.get(path, params={'product_url': url + '/'})
    assert response.status_code == 200
    fields = ('stock_status', 'stock_quantity', 'game', 'card_number', 'rarity')
    assert tuple(response.json()[field] for field in fields) == expected
    assert response.headers['cache-control'] == 'no-store'
    assert client.get(path, params={'product_url': 'https://example.org/shopdetail/1'}).status_code == 422
    assert client.get(path, params={'product_url': url + '?other=1'}).status_code == 422
    client.headers.pop('Authorization')
    assert client.get(path, params={'product_url': url}).status_code == 401
    assert FakeClient.calls == [url]

def test_stock_reads_fresh_product_page_without_polluting_details_cache(client):
    params = {'gid': 56, 'ver': 'OS01', 'cid': '10240'}
    details = client.get('/api/yuyutei/product', params=params).json()
    assert (details['card_number'], details['rarity']) == ('OS01/R01-053SEC', 'SEC')
    for quantity in (25, 0):
        FakeClient.reply = FIXTURE + offer_html(quantity=quantity)
        response = client.get('/api/yuyutei/product', params={**params, 'stock': 'true'})
        assert response.status_code == 200
        assert response.json()['stock_quantity'] == quantity
        assert response.json()['stock_status'] == ('in_stock' if quantity else 'out_of_stock')
        assert response.headers['cache-control'] == 'no-store'
    assert client.get('/api/yuyutei/product', params=params).json() == details
    assert len(FakeClient.calls) == 3


def offer_html(price='1,280', quantity='5', kizu=0):
    return (f'<div class="offer"><div class="lhs"><h4>{price} 円</h4></div>'
            f'<div class="rhs"><div class="counter"><input class="cart_kizu" value="{kizu}">'
            f'<input class="cart_active" value="{quantity}"></div></div></div>')


def test_subscription_snapshot_price_stock_and_condition_isolation():
    url = 'https://yuyu-tei.jp/sell/wsr/card/OS01/10240'
    html = FIXTURE + offer_html() + offer_html('580', '2', kizu=1)
    result = module.parse_product_snapshot(html, 'wsr', url)
    assert result['price'] == 1280 and result['stock_quantity'] == 5
    assert result['stock_status'] == 'in_stock' and result['currency'] == 'JPY'
    assert result['name'] == '大人のいい女 美羽(サイン入り)'
    assert result['rarity'] == 'SEC' and result['product_url'] == url
    result = module.parse_product_snapshot(FIXTURE + offer_html('0', '0'), 'wsr', url)
    assert result['price'] == 0 and result['stock_status'] == 'out_of_stock'


@pytest.mark.parametrize('offers', [
    offer_html(kizu=1), offer_html('100', ''),
])
def test_invalid_snapshot_never_turns_unknown_into_zero(offers):
    with pytest.raises(ValueError):
        module.parse_product_snapshot(FIXTURE + offers, 'wsr', 'https://yuyu-tei.jp/sell/wsr/card/OS01/10240')
