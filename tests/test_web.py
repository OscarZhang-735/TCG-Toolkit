from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.catalog.providers.cardmax import CardMaxSearcher
from tcg_toolkit.web import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def authenticated_business_api():
    from tcg_toolkit.auth.dependencies import current_user
    app.dependency_overrides[current_user] = lambda: {"id": "test-user"}
    yield
    app.dependency_overrides.pop(current_user, None)


def test_unsupported_merchant_game():
    params = dict(source='cardmax', game='wsr', keyword='x')
    assert client.get('/api/search', params=params).status_code == 422


def test_cardmax_search(monkeypatch):
    def respond(request):
        assert dict(request.url.params) == {'search': 'test', 'category': 'ct1740'}
        html = (Path(__file__).parent / 'fixtures/cardmax_sev.html').read_text(encoding='utf-8')
        return httpx.Response(200, text=html)
    monkeypatch.setattr(CardMaxSearcher, 'client_type', staticmethod(
        lambda: httpx.Client(transport=httpx.MockTransport(respond))))
    response = client.get('/api/search', params={'source': 'cardmax', 'game': 'sev', 'keyword': 'test'})
    assert response.status_code == 200
    data = response.json()
    assert data['source'] == 'cardmax'
    assert data['items'][0]['card_number'] == 'BP22-T03'
    assert data['next_page'] == 2


def test_hobbystation_search(monkeypatch):
    def get(self, url, *, timeout):
        assert url == 'https://www.hobbystation-single.jp/lo/product/list?search_word=abc&match_type=1'
        html = '<main><div class="ec-searchnavRole__counter">お探しの商品は見つかりませんでした</div></main>'
        return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr('tcg_toolkit.external.search_proxy.SearchProxyClient.get', get)
    result = client.get('/api/search', params={'source': 'hobbystation', 'game': 'lycee', 'keyword': 'abc'})
    assert result.status_code == 200
    assert result.json() == dict(source='hobbystation', game='lycee', keyword='abc', page=1,
                                 items=[], next_page=None, has_next=False)


def test_default_search_returns_yuyutei_cart_identity(monkeypatch):
    html = (Path(__file__).parent / 'fixtures/sev_1.html').read_text(encoding='utf-8')
    def get(self, url, *, timeout):
        assert url == 'https://yuyu-tei.jp/sell/sev/s/search?search_word=BP01'
        return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr('tcg_toolkit.external.search_proxy.SearchProxyClient.get', get)
    response = client.get('/api/search', params={'game': 'sev', 'keyword': 'BP01'})
    assert response.status_code == 200
    item = response.json()['items'][0]
    assert item['game'] == 'sev' and item['card_number'] == 'BP01-U01'
    assert item['cart']['gid'] == 47
