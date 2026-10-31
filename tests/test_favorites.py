from auth_helpers import register_user
import json
import sqlite3
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.models import Game, SearchItem, SearchPage, StockStatus
from tcg_toolkit.errors import SearchRequestError, SearchParseError
from tcg_toolkit.web import app, SEARCHERS

ORIGIN = 'https://testserver'


def card(game=Game.WSR):
    code = {Game.WSR: 'wsr', Game.LYCEE: 'lo', Game.CHAOS: 'chaos', Game.WS: 'ws', Game.PM: 'pm', Game.SEV: 'sev'}[game]
    return SearchItem(game, 'Card', 'ABC-001', 100, 'JPY', StockStatus.IN_STOCK,
                      2, '2', f'https://yuyu-tei.jp/sell/{code}/card/set/001')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TCG_USER_DB', str(tmp_path / 'users.sqlite3'))
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
    assert add(client, replace(card(), price=999)) == saved
    assert client.get('/api/favorites').json()['items'] == [saved]
    reopened = UserStore(app.state.auth.store.path)
    with reopened.connect() as db:
        assert json.loads(db.execute('SELECT snapshot FROM favorites').fetchone()[0])['price'] == 100
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
    class Fake:
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


@pytest.mark.parametrize('failure,status', [('missing', 404), ('request', 502), ('parse', 502), ('timeout', 504), ('removed', 404)])
def test_refresh_failures_preserve_snapshot(client, monkeypatch, failure, status):
    saved = add(client)
    class Fake:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_pages(self, keyword):
            if failure == 'removed':
                with app.state.auth.store.connect() as db:
                    db.execute('DELETE FROM favorites WHERE id = ?', (saved['id'],))
                yield SearchPage([card()], keyword, 1, None)
            elif failure == 'missing':
                yield SearchPage([], keyword, 1, None)
            else:
                error = SearchParseError if failure == 'parse' else SearchRequestError
                raise error('upstream failure', game=Game.WSR, keyword=keyword, page=1, url='source') from (httpx.ReadTimeout('timeout') if failure == 'timeout' else None)
    monkeypatch.setitem(SEARCHERS, Game.WSR, Fake)
    assert client.post(f"/api/favorites/{saved['id']}/refresh", json={}).status_code == status
    assert client.get('/api/favorites').json()['items'] == ([] if failure == 'removed' else [saved])


def test_v1_migration_preserves_existing_data(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, password_hash TEXT, created_at INTEGER);
            CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT, expires_at INTEGER);
            INSERT INTO users VALUES ('u', 'alice', 'hash', 1);
            INSERT INTO sessions VALUES ('token', 'u', 9999999999);
            PRAGMA user_version = 1;''')
    with UserStore(path).connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 8
        assert db.execute('SELECT username FROM users').fetchone()[0] == 'alice'
        assert db.execute('SELECT token_hash FROM sessions').fetchone()[0] == 'token'
        assert db.execute('SELECT count(*) FROM favorites').fetchone()[0] == 0
