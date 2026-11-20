from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select, delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tcg_toolkit.persistence.models import Favorite, FavoriteGroup, Subscription
from auth_helpers import register_user
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.persistence.runtime import open_database
from tcg_toolkit.catalog.providers.cardmax import CardMaxSEVSearcher
from tcg_toolkit.catalog.models import Game, SearchItem, SearchPage, StockStatus
from tcg_toolkit.catalog.errors import SearchRequestError, SearchParseError
from tcg_toolkit.catalog.pagination import PagedSearcher
from tcg_toolkit.catalog.search import SEARCHERS
from tcg_toolkit.catalog.providers.hobbystation import HobbyStationLyceeSearcher
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'


def card(game=Game.WSR):
    code = {Game.WSR: 'wsr', Game.LYCEE: 'lo', Game.CHAOS: 'chaos', Game.WS: 'ws', Game.PM: 'pm', Game.SEV: 'sev'}[game]
    return SearchItem(game, 'Card', 'ABC-001', 100, 'JPY', StockStatus.IN_STOCK,
                      2, '2', f'https://yuyu-tei.jp/sell/{code}/card/set/001')


@pytest.fixture
def client(tmp_path, monkeypatch):
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
    reopened = open_database(app.state.database.conninfo)
    with reopened.session() as db:
        assert json.loads(db.scalar(select(Favorite.snapshot)))['price'] == 100
    login(client, 'bob')
    assert client.get('/api/favorites').json() == {'items': []}
    for method, path in [('delete', ''), ('post', '/refresh')]:
        response = getattr(client, method)(f"/api/favorites/{saved['id']}{path}", json={}) if method == 'post' else client.request('DELETE', f"/api/favorites/{saved['id']}", json={})
        assert response.status_code == 404
    other = add(client)
    assert other['id'] != saved['id']
    assert client.request('DELETE', f"/api/favorites/{other['id']}", json={}).status_code == 204
    assert client.get('/api/favorites').json()['items'] == []


def test_access_and_validation(client):
    assert client.post('/api/favorites', json=card().to_dict(), headers={'Origin': 'https://evil.test'}).status_code == 403
    assert client.post('/api/favorites', content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    for url in ['https://evil.test/sell/wsr/card/a', 'https://yuyu-tei.jp/sell/lo/card/a', 'https://yuyu-tei.jp/sell/wsr/card/../a']:
        assert client.post('/api/favorites', json={**card().to_dict(), 'product_url': url}).status_code == 422
    client.headers.pop("Authorization", None)
    for method, url in [('GET', '/api/favorites'), ('POST', '/api/favorites'), ('DELETE', '/api/favorites/x'), ('POST', '/api/favorites/x/refresh')]:
        response = client.request(method, url, json=card().to_dict())
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('game', list(Game))
def test_refresh_exact_match_across_pages(client, monkeypatch, game):
    item = card(game)
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
    monkeypatch.setitem(SEARCHERS, game, Fake)
    response = client.post(f"/api/favorites/{saved['id']}/refresh", json={})
    assert response.status_code == 200
    assert response.json()['item'] == updated.to_dict()
    assert response.json()['refreshed_at'] is not None
    assert add(client, item) == response.json()


def test_favorite_preserves_cart_condition_on_save_and_refresh(client, monkeypatch):
    normal = replace(card(), cart={'gid': 56, 'ver': 'set', 'cid': '001', 'kizu': 0})
    damaged = replace(normal, cart={**normal.cart, 'kizu': 1})
    saved = add(client, damaged)
    assert saved['item']['cart'] == damaged.cart

    class Fake(PagedSearcher):
        def __init__(self, *, stock): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_pages(self, keyword):
            yield SearchPage([normal, replace(damaged, price=50)], keyword, 1, None)

    monkeypatch.setitem(SEARCHERS, Game.WSR, Fake)
    refreshed = client.post(f"/api/favorites/{saved['id']}/refresh", json={}).json()['item']
    assert refreshed['cart'] == damaged.cart
    assert refreshed['price'] == 50


@pytest.mark.parametrize('failure,status', [('missing', 404), ('request', 502), ('parse', 502), ('timeout', 504), ('removed', 404)])
def test_refresh_failures_preserve_snapshot(client, monkeypatch, failure, status):
    saved = add(client)
    class Fake(PagedSearcher):
        def __init__(self, *, stock): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_pages(self, keyword):
            if failure == 'removed':
                with app.state.database.session() as db:
                    db.execute(delete(Favorite).where(Favorite.id == saved['id']))
                yield SearchPage([card()], keyword, 1, None)
            elif failure == 'missing':
                yield SearchPage([], keyword, 1, None)
            else:
                error = SearchParseError if failure == 'parse' else SearchRequestError
                raise error('upstream failure', game=Game.WSR, keyword=keyword, page=1, url='source') from (httpx.ReadTimeout('timeout') if failure == 'timeout' else None)
    monkeypatch.setitem(SEARCHERS, Game.WSR, Fake)
    assert client.post(f"/api/favorites/{saved['id']}/refresh", json={}).status_code == status
    assert client.get('/api/favorites').json()['items'] == ([] if failure == 'removed' else [saved])


def group(client, name='Custom'):
    response = client.post('/api/favorites/groups', json={'name': name})
    assert response.status_code == 201
    return response.json()


def groups(client):
    response = client.get('/api/favorites/groups')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    return response.json()['items']


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


@pytest.mark.parametrize('url,game,extra', [
    ('https://www.hobbystation-single.jp/ws/product/detail/1', 'wsr', {}),
    ('https://www.hobbystation-single.jp/ws/product/detail/1', 'ws', {'source': 'cardmax'}),
    ('https://www.cardmax.jp/shopdetail/00001', 'wsr', {}),
    ('http://www.cardmax.jp/shopdetail/00001', 'ws', {}),
    ('https://www.cardmax.jp/shopdetail/00001?other=1', 'ws', {}),
    ('https://www.cardmax.jp/shopdetail/00001', 'ws',
     {'cart': {'gid': 7, 'ver': 'set', 'cid': '001', 'kizu': 0}}),
])
def test_merchant_favorite_validation(client, url, game, extra):
    response = client.post('/api/favorites', json={**card().to_dict(), 'game': game,
                           'product_url': url, **extra})
    assert response.status_code == 422


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


@pytest.mark.parametrize('created_at', [(100, 100, 100), (100, 101, 102)])
def test_group_lifecycle_and_name_rules(client, monkeypatch, created_at):
    # Exercise UUID tie-breaking without depending on request speed or random IDs.
    timestamps, ids = iter(created_at), iter([3, 2, 1])
    monkeypatch.setattr('tcg_toolkit.collection.groups.time', SimpleNamespace(time=lambda: next(timestamps)))
    monkeypatch.setattr('tcg_toolkit.collection.groups.uuid', SimpleNamespace(uuid4=lambda: UUID(int=next(ids))))
    assert groups(client) == []
    for name in ['', ' \t\n', 'x' * 101, None, 42]:
        assert client.post('/api/favorites/groups', json={'name': name}).status_code == 422
    first = group(client, '  Custom  ')
    assert first['name'] == 'Custom' and set(first) == {'id', 'name', 'created_at'}
    second = group(client, 'custom')  # Names are case sensitive.
    third = group(client, ' ' + '长' * 100 + ' ')
    for name in ['Custom', ' Custom ']:
        response = client.post('/api/favorites/groups', json={'name': name})
        assert response.status_code == 409
        assert response.json()['detail'] == 'favorite_group_name_taken'
    path = f"/api/favorites/groups/{first['id']}"
    assert client.put(path, json={'name': 'custom'}).status_code == 409
    assert client.put(path, json={'name': '  '}).status_code == 422
    assert client.put(path, json={'name': 'Custom'}).json() == first
    renamed = client.put(path, json={'name': ' Renamed '}).json()
    assert renamed == {**first, 'name': 'Renamed'}
    rows = groups(client)
    assert rows == sorted([renamed, second, third], key=lambda row: (row['created_at'], row['id']))
    assert client.request('DELETE', f"/api/favorites/groups/{second['id']}", json={}).status_code == 204
    assert client.get('/api/favorites', params={'group_id': second['id']}).status_code == 404


def test_membership_filter_move_and_delete_preserve_records(client):
    custom = group(client)
    saved = add(client)
    assert saved['group_id'] is None
    item = replace(card(), product_url=card().product_url + '2')
    response = client.post('/api/favorites', json={**item.to_dict(), 'group_id': custom['id']})
    assert response.status_code == 200
    other = response.json()
    assert other['group_id'] == custom['id']
    assert other['item'] == {key: value for key, value in item.to_dict().items() if key != 'cart'}
    assert client.get('/api/favorites').json()['items'] == [other, saved]
    assert client.get('/api/favorites', params={'group_id': custom['id']}).json()['items'] == [other]
    assert client.post('/api/favorites', json={**card().to_dict(), 'group_id': custom['id']}).json() == saved
    assert client.post('/api/favorites', json={**item.to_dict(), 'group_id': None}).json() == other
    path = f"/api/favorites/{saved['id']}/group"
    for body in [{}, {'group_id': ''}]:
        assert client.put(path, json=body).status_code == 422
    moved = client.put(path, json={'group_id': custom['id']}).json()
    assert moved == {**saved, 'group_id': custom['id']}
    assert client.put(path, json={'group_id': custom['id']}).json() == moved
    assert client.put(path, json={'group_id': None}).json() == saved
    moved = client.put(path, json={'group_id': custom['id']}).json()
    with app.state.database.session() as db:
        row = db.get(Favorite, saved['id'])
        db.add(Subscription(id='sub', user_id=row.user_id, game=row.game, product_url=row.product_url,
                            snapshot=row.snapshot, created_at=1, checked_at=1, next_check_at=86401))
    assert client.request('DELETE', f"/api/favorites/groups/{custom['id']}", json={}).status_code == 204
    assert client.get('/api/favorites').json()['items'] == [{**other, 'group_id': None}, saved]
    with app.state.database.session() as db:
        assert db.get(Subscription, 'sub').snapshot == json.dumps(saved['item'], separators=(',', ':'))
    assert client.request('DELETE', f"/api/favorites/groups/{custom['id']}", json={}).status_code == 404


def test_groups_are_owned_and_database_rejects_cross_account_membership(client):
    alice_group, saved = group(client), add(client)
    login(client, 'bob')
    assert groups(client) == []
    bob_group = group(client)
    bob_saved = add(client)
    for group_id in [alice_group['id'], 'missing']:
        calls = [
            ('GET', '/api/favorites', {'params': {'group_id': group_id}}),
            ('POST', '/api/favorites', {'json': {**card().to_dict(), 'group_id': group_id}}),
            ('PUT', f'/api/favorites/groups/{group_id}', {'json': {'name': 'Other'}}),
            ('DELETE', f'/api/favorites/groups/{group_id}', {'json': {}}),
            ('PUT', f"/api/favorites/{bob_saved['id']}/group", {'json': {'group_id': group_id}}),
        ]
        for method, path, kwargs in calls:
            response = client.request(method, path, **kwargs)
            assert response.status_code == 404
            assert response.json()['detail'] == 'favorite_group_not_found'
    for favorite_id in [saved['id'], 'missing']:
        response = client.put(f'/api/favorites/{favorite_id}/group', json={'group_id': bob_group['id']})
        assert response.status_code == 404
        assert response.json()['detail'] == 'favorite_not_found'
    with pytest.raises(IntegrityError):
        with app.state.database.session() as db:
            db.execute(update(Favorite).where(Favorite.id == saved['id']).values(group_id=bob_group['id']))


@pytest.mark.parametrize('method,path,body', [
    ('GET', '/api/favorites/groups', None),
    ('POST', '/api/favorites/groups', {'name': 'Custom'}),
    ('PUT', '/api/favorites/groups/x', {'name': 'Custom'}),
    ('DELETE', '/api/favorites/groups/x', {}),
    ('PUT', '/api/favorites/x/group', {'group_id': 'x'}),
])
def test_group_access_guards(client, method, path, body):
    if method != 'GET':
        assert client.request(method, path, json=body, headers={'Origin': 'https://evil.test'}).status_code == 403
        assert client.request(method, path, content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    client.headers.pop('Authorization')
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'


def test_group_delete_rolls_back_membership_on_failure(client, monkeypatch):
    custom = group(client)
    saved = client.post('/api/favorites', json={**card().to_dict(), 'group_id': custom['id']}).json()
    original = Session.delete
    def fail_delete(self, instance):
        if isinstance(instance, FavoriteGroup):
            raise RuntimeError('simulated group deletion failure')
        return original(self, instance)
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'delete', fail_delete)
        with pytest.raises(RuntimeError, match='simulated group deletion failure'):
            client.request('DELETE', f"/api/favorites/groups/{custom['id']}", json={})
    assert client.get('/api/favorites', params={'group_id': custom['id']}).json()['items'] == [saved]


@pytest.mark.parametrize('action', ['add', 'move'])
def test_concurrent_group_delete_and_membership_write(client, action):
    custom = group(client)
    saved = add(client) if action == 'move' else None
    barrier = Barrier(2)
    def remove():
        barrier.wait(timeout=10)
        return client.request('DELETE', f"/api/favorites/groups/{custom['id']}", json={})
    def write():
        barrier.wait(timeout=10)
        if action == 'add':
            return client.post('/api/favorites', json={**card().to_dict(), 'group_id': custom['id']})
        return client.put(f"/api/favorites/{saved['id']}/group", json={'group_id': custom['id']})
    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion, writing = pool.submit(remove), pool.submit(write)
        assert deletion.result(timeout=15).status_code == 204
        result = writing.result(timeout=15)
    assert result.status_code in (200, 404)
    rows = client.get('/api/favorites').json()['items']
    assert len(rows) == (1 if action == 'move' or result.status_code == 200 else 0)
    assert all(row['group_id'] is None for row in rows)
    if saved:
        assert rows == [saved]


def test_concurrent_duplicate_group_creation(client):
    barrier = Barrier(2)
    def create():
        barrier.wait(timeout=10)
        return client.post('/api/favorites/groups', json={'name': 'Same'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sorted(response.status_code for response in results) == [201, 409]
