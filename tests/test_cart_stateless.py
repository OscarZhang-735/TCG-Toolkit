from auth_helpers import create_v6_database
import json
from contextlib import contextmanager
import sqlite3

import pytest

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.web import app
from test_yuyutei_cart import client, upstream, BASE, CARD, SECRETS, ORIGIN


@pytest.mark.parametrize('method,path', [('POST', '/cart/query'), ('POST', '/cart'), ('DELETE', '/cart')])
def test_missing_credentials_no_query_or_cookie_fallback(client, upstream, method, path):
    client.cookies.set('laravel_session', SECRETS['cookie'])
    response = client.request(method, BASE + path, params=SECRETS, json=CARD if path == '/cart' else {})
    assert response.status_code == 422
    assert response.json() == {'detail': 'invalid_yuyutei_request'}
    assert not upstream[0]


@pytest.mark.parametrize('method,path', [('POST', '/cart/query'), ('POST', '/cart'), ('DELETE', '/cart')])
def test_extra_fields_do_not_echo_nested_credentials(client, upstream, method, path):
    response = client.request(method, BASE + path, json={
        **(CARD if path == '/cart' else {}), 'credentials': SECRETS, 'extra': 'private-input'})
    assert response.status_code == 422
    assert response.json() == {'detail': 'invalid_yuyutei_request'}
    assert not upstream[0]


def test_legacy_routes_cannot_store_or_return_credentials(client, upstream):
    for method in ['GET', 'PUT', 'DELETE']:
        response = client.request(method, BASE + '/credentials', json=SECRETS)
        assert response.status_code in (404, 405)
        assert SECRETS['cookie'] not in response.text
    assert client.get(BASE + '/cart').status_code in (404, 405)
    assert not upstream[0]


def test_idempotency_excludes_credentials_and_still_requires_valid_envelope(client, upstream):
    headers = {'Idempotency-Key': 'stateless-key'}
    first = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=headers)
    assert first.status_code == 200
    second = client.post(BASE + '/cart', json={**CARD, 'credentials': {
        'cookie': 'different-cookie', 'csrf_token': 'different-csrf'}}, headers=headers)
    assert second.status_code == 200 and second.json() == first.json()
    assert second.headers['idempotency-replayed'] == 'true'
    assert client.post(BASE + '/cart', json=CARD, headers=headers).status_code == 422
    assert sum(r.method == 'POST' for r in upstream[0]) == 1
    raw = app.state.auth.store.path.read_bytes()
    assert not any(value.encode() in raw for value in SECRETS.values())


def test_v6_migration_erases_old_and_freed_secrets_preserves_operations(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    create_v6_database(path)
    old_secret = 'OLD-SECRET-' * 1000
    current_secret = 'CURRENT-COOKIE-SECRET'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA secure_delete=OFF')
        db.execute("INSERT INTO users VALUES ('u','alice','hash',1)")
        db.execute("INSERT INTO sessions VALUES ('session-hash','u',9999999999)")
        db.execute("INSERT INTO favorites VALUES ('f','u','ws','url','{}',1,NULL)")
        db.execute("CREATE TABLE yuyutei_credentials (user_id TEXT, session_cookie TEXT, csrf_token TEXT, updated_at INTEGER)")
        db.execute('INSERT INTO yuyutei_credentials VALUES (?, ?, ?, ?)', ('u', old_secret, 'old-token-secret', 1))
        db.execute('UPDATE yuyutei_credentials SET session_cookie=?, csrf_token=?', (current_secret, 'current-token-secret'))
        db.execute("INSERT INTO cart_operations VALUES ('u','done','digest','complete',200,'{}',1,1)")
        db.execute("INSERT INTO cart_operations VALUES ('u','pending','digest','processing',NULL,NULL,1,1)")
    for _ in range(2):
        with UserStore(path).connect() as db:
            assert db.execute('PRAGMA user_version').fetchone()[0] == 8
            assert not db.execute("SELECT name FROM sqlite_master WHERE name='yuyutei_credentials'").fetchone()
            assert db.execute('SELECT count(*) FROM users').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM favorites').fetchone()[0] == 1
            assert [tuple(row) for row in db.execute('SELECT operation_key,state FROM cart_operations ORDER BY operation_key')] == [('done','complete'), ('pending','processing')]
    raw = path.read_bytes()
    for secret in [old_secret, current_secret, 'old-token-secret', 'current-token-secret']:
        assert secret.encode() not in raw


def test_failed_credential_cleanup_is_retried_on_restart(tmp_path):
    path = tmp_path / 'interrupted.sqlite3'
    create_v6_database(path)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE yuyutei_credentials (secret TEXT)')
        db.execute("INSERT INTO yuyutei_credentials VALUES ('legacy-credential')")

    class InterruptedStore(UserStore):
        @contextmanager
        def connect(self):
            with super().connect() as db:
                class Connection:
                    def execute(self, sql, *args):
                        if sql == 'VACUUM':
                            raise sqlite3.OperationalError('simulated cleanup failure')
                        return db.execute(sql, *args)
                yield Connection()

    with pytest.raises(sqlite3.OperationalError, match='cleanup failure'):
        InterruptedStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 6
    UserStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 8
    assert b'legacy-credential' not in path.read_bytes()
