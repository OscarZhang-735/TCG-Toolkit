from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from auth_helpers import register_user
from tcg_toolkit.catalog.models import Game, SearchItem, SearchPage, StockStatus
from tcg_toolkit.catalog.pagination import PagedSearcher
from tcg_toolkit.catalog.providers.cardmax import CardMaxSEVSearcher
from tcg_toolkit.catalog.providers.hobbystation import HobbyStationLyceeSearcher
from tcg_toolkit.catalog.search import SEARCHERS
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'


def card(game=Game.WSR):
    code = {Game.WSR: 'wsr', Game.LYCEE: 'lo', Game.CHAOS: 'chaos', Game.WS: 'ws', Game.PM: 'pm', Game.SEV: 'sev'}[game]
    return SearchItem(game, 'Card', 'ABC-001', 100, 'JPY', StockStatus.IN_STOCK,
                      2, '2', f'https://yuyu-tei.jp/sell/{code}/card/set/001')


@pytest.fixture
def client(monkeypatch, postgres_schema):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        login(client, 'alice')
        yield client


def login(client, username):
    response = register_user(client, username)
    assert response.status_code == 201
    client.headers["Authorization"] = "Bearer " + response.json()["token"]
    return response.json()["user"]


def add(client, item=None):
    response = client.post('/api/favorites', json=(item or card()).to_dict())
    assert response.status_code == 200
    return response.json()


def test_persistence_idempotency_and_isolation(client):
    saved = add(client)
    assert saved['source'] == 'yuyutei'
    assert add(client, replace(card(), price=999)) == saved
    assert client.get('/api/favorites').json()['items'] == [saved]
    login(client, 'bob')
    assert client.get('/api/favorites').json() == {'items': []}
    for method, suffix in [('DELETE', ''), ('POST', '/refresh')]:
        response = client.request(method, f"/api/favorites/{saved['id']}{suffix}", json={})
        assert response.status_code == 404
    other = add(client)
    assert other['id'] != saved['id']
    assert client.request('DELETE', f"/api/favorites/{other['id']}", json={}).status_code == 204
    assert client.get('/api/favorites').json()['items'] == []


def test_refresh_exact_match_across_pages(client, monkeypatch):
    item = card()
    saved = add(client, item)
    updated = replace(item, price=250, stock_quantity=0, stock_status=StockStatus.OUT_OF_STOCK)
    class Fake(PagedSearcher):
        def __init__(self, *, stock): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_pages(self, keyword):
            assert keyword == item.card_number
            yield SearchPage([replace(item, product_url=item.product_url + '2')], keyword, 1, 2)
            yield SearchPage([updated], keyword, 2, None)
    monkeypatch.setitem(SEARCHERS, item.game, Fake)
    response = client.post(f"/api/favorites/{saved['id']}/refresh", json={})
    assert response.status_code == 200
    assert response.json()['item'] == updated.to_dict()
    assert response.json()['refreshed_at'] is not None
    assert add(client, item) == response.json()


def group(client, name='Custom'):
    response = client.post('/api/favorites/groups', json={'name': name})
    assert response.status_code == 201
    return response.json()


def test_three_shops_share_a_group_and_keep_separate_favorites(client):
    group_id = group(client)['id']
    snapshot = {**card(Game.WS).to_dict(), 'group_id': group_id}
    urls = {
        'yuyutei': snapshot['product_url'],
        'hobbystation': 'https://www.hobbystation-single.jp/ws/product/detail/123',
        'cardmax': 'https://www.cardmax.jp/shopdetail/000000123',
    }
    saved_ids = {}
    for source, url in urls.items():
        response = client.post('/api/favorites', json={**snapshot, 'product_url': url})
        assert response.status_code == 200
        saved = response.json()
        assert saved['source'] == source and saved['group_id'] == group_id
        saved_ids[source] = saved['id']
        duplicate_url = url if source == 'yuyutei' else url + '/'
        duplicate = client.post('/api/favorites', json={**snapshot, 'price': 200, 'group_id': None,
                                'source': source, 'product_url': duplicate_url})
        assert duplicate.json() == saved
    assert len(set(saved_ids.values())) == 3
    group_url = '/api/favorites?group_id=' + group_id
    assert {row['source'] for row in client.get(group_url).json()['items']} == set(urls)
    hobby = '/api/favorites/' + saved_ids['hobbystation']
    assert client.put(hobby + '/group', json={'group_id': None}).json()['group_id'] is None
    assert client.request('DELETE', '/api/favorites/' + saved_ids['cardmax'], json={}).status_code == 204
    assert [row['source'] for row in client.get(group_url).json()['items']] == ['yuyutei']


def test_hobby_refresh_preserves_exact_variant_and_image_proxy(client, monkeypatch):
    html = (Path(__file__).parent / 'fixtures/hobbystation_lo.html').read_text(encoding='utf-8')
    def respond(request):
        assert request.url.path == '/lo/product/list'
        return httpx.Response(200, text=html)
    monkeypatch.setattr(HobbyStationLyceeSearcher, 'client_type', staticmethod(
        lambda: httpx.Client(transport=httpx.MockTransport(respond))))
    search = client.get('/api/search', params={'source': 'hobbystation', 'game': 'lycee', 'keyword': 'LO-4899'})
    parallel = search.json()['items'][1]
    saved = client.post('/api/favorites', json={**parallel, 'price': 1}).json()
    row = client.post(f"/api/favorites/{saved['id']}/refresh", json={}).json()
    assert row['source'] == 'hobbystation'
    assert row['item'] == parallel
    assert row['item']['image_url'] == ORIGIN + '/api/images/hobbystation/lo/LO-LO-4899-K.jpg'
    assert row['item']['stock_status'] == 'in_stock' and row['item']['stock_quantity'] is None
    assert client.get('/api/favorites').json()['items'] == [row]


def test_cardmax_refresh_reads_only_saved_detail_and_preserves_snapshot_on_failure(client, monkeypatch):
    html = (Path(__file__).parent / 'fixtures/cardmax_sev.html').read_text(encoding='utf-8')
    detail = '/shopdetail/000000235103'
    paths = []
    responses = iter([httpx.Response(200, text='<span class="M_item-stock-soldout">売り切れ</span>'),
                      httpx.Response(503)])
    def respond(request):
        paths.append(request.url.path)
        if request.url.path == detail:
            return next(responses)
        assert request.url.path == '/shop/shopbrand.html'
        assert dict(request.url.params) == {'category': 'ct1740', 'search': 'BP22-014'}
        return httpx.Response(200, text=html)
    monkeypatch.setattr(CardMaxSEVSearcher, 'client_type', staticmethod(
        lambda: httpx.Client(transport=httpx.MockTransport(respond))))
    saved = add(client, replace(card(Game.SEV), card_number='BP22-014', product_url='https://www.cardmax.jp' + detail))
    path = f"/api/favorites/{saved['id']}/refresh"
    updated = client.post(path, json={}).json()
    assert paths == ['/shop/shopbrand.html', detail]
    assert updated['source'] == 'cardmax'
    assert updated['item']['price'] == 30
    assert (updated['item']['stock_status'], updated['item']['stock_quantity']) == ('out_of_stock', 0)
    assert client.post(path, json={}).status_code == 502
    assert client.get('/api/favorites').json()['items'] == [updated]
