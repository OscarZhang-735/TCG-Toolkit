from concurrent.futures import ThreadPoolExecutor
from threading import Event
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit import cart_operations
from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.web import app
from tcg_toolkit.yuyutei_cart import CartInput
from test_yuyutei_cart import (client, upstream, login, cart_html, cart_sequence,
                              ORIGIN, BASE, CARD, SECRETS, CART_META, EMPTY_CART)

KEY = {'Idempotency-Key': 'test-operation-1'}
SUCCESS = (200, '{"status":"SUCCESS"}')
REMOVED = (200, '{"CART_ITEM_ID":"itemValue5","GAME_ID":"56","RESERVED_CHECK":0}')


@pytest.mark.parametrize('after,expected', [(5, 200), (4, 409), (3, 409), (7, 409)])
def test_observed_increment(client, monkeypatch, after, expected):
    calls = cart_sequence(monkeypatch, [(200, cart_html(quantity=3)), SUCCESS,
                                        (200, cart_html(quantity=after))])
    response = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS, 'counter': 2}, headers=KEY)
    assert response.status_code == expected
    result = response.json() if expected == 200 else response.json()['detail']
    assert result['requested_quantity'] == 2
    assert result['before_quantity'] == 3 and result['after_quantity'] == after
    assert result['verification'] == ('verified' if expected == 200 else 'mismatch')
    replay = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS, 'counter': 2}, headers=KEY)
    assert replay.status_code == expected and replay.json() == response.json()
    assert [r.method for r in calls] == ['GET', 'POST', 'GET']


@pytest.mark.parametrize('after,expected', [
    (httpx.ReadTimeout('private-error'), 504),
    ((200, EMPTY_CART.replace('alice-csrf', 'new-session')), 409),
    ((200, CART_META + '<html>bad page</html>'), 502)])
def test_add_verification_unknown_is_durable(client, monkeypatch, after, expected):
    calls = cart_sequence(monkeypatch, [(200, EMPTY_CART), SUCCESS, after])
    first = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY)
    assert first.status_code == expected
    detail = first.json()['detail']
    assert detail['upstream_status'] == 'SUCCESS'
    assert detail['verification'] == 'unknown' and detail['outcome'] == 'unknown'
    assert detail['before_quantity'] == 0 and detail['after_quantity'] is None
    assert 'private-error' not in first.text
    assert client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY).json() == first.json()
    assert sum(r.method == 'POST' for r in calls) == 1


def test_no_baseline_no_add(client, monkeypatch):
    calls = cart_sequence(monkeypatch, [(200, cart_html().replace('value="30"', 'value="unknown"'))])
    response = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY)
    assert response.status_code == 502
    assert response.json()['detail']['outcome'] == 'not_sent'
    assert [r.method for r in calls] == ['GET']


def test_query_and_add_do_not_require_delete_button(client, monkeypatch):
    before = cart_html().replace('removeItem(', 'otherAction(')
    after = cart_html(quantity=4).replace('removeItem(', 'otherAction(')
    calls = cart_sequence(monkeypatch, [(200, before), (200, before), SUCCESS, (200, after), (200, after)])
    query = client.post(BASE + '/cart/query', json={'credentials': SECRETS}).json()
    assert query['complete'] and not query['warnings']
    assert query['items'][0]['removable'] is False
    assert query['total_quantity'] == 3
    assert client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}).json()['verification'] == 'verified'
    assert client.request('DELETE', BASE + '/cart', json={**CARD, 'credentials': SECRETS}).status_code == 502
    assert sum(r.method == 'POST' for r in calls) == 1


@pytest.mark.parametrize('bad', [
    cart_html(6).replace('value="30"', 'value="invalid"'),
    cart_html(6).replace('/sell/wsr/card/OS01/10044', '/unsupported/product'),
    cart_html(6).replace('id="itemInfo6"', 'id="itemInfoUnknown"')])
def test_partial_query_never_claims_complete_totals_or_allows_removal(client, monkeypatch, bad):
    mixed = cart_html().replace('</div></div>', '</div>' + bad.replace(CART_META, '') + '</div>')
    calls = cart_sequence(monkeypatch, [(200, mixed), (200, mixed)])
    result = client.post(BASE + '/cart/query', json={'credentials': SECRETS}).json()
    assert len(result['items']) == 1
    assert result['complete'] is False
    assert result['total_quantity'] is None and result['subtotal'] is None
    assert result['warnings'] == [{'row_index': 1, 'code': 'unrecognized_cart_item'}]
    assert client.request('DELETE', BASE + '/cart', json={**CARD, 'credentials': SECRETS}).status_code == 502
    assert all(r.method == 'GET' for r in calls)


def test_unreadable_target_after_delete_is_not_absence(client, monkeypatch):
    bad = cart_html().replace('value="30"', 'value="invalid"')
    calls = cart_sequence(monkeypatch, [(200, bad), (200, cart_html()), REMOVED, (200, bad)])
    result = client.post(BASE + '/cart/query', json={'credentials': SECRETS}).json()
    assert result['items'] == [] and result['complete'] is False and result['subtotal'] is None
    response = client.request('DELETE', BASE + '/cart', json={**CARD, 'credentials': SECRETS})
    assert response.status_code == 502
    assert response.json()['detail']['outcome'] == 'unknown'
    assert sum(r.method == 'POST' for r in calls) == 1


def test_replay_normalized_request_after_restart_with_changed_credentials(client, upstream):
    first = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY)
    assert first.status_code == 200
    with TestClient(app, base_url=ORIGIN, headers=dict(client.headers)) as restarted:
        replay = restarted.post(BASE + '/cart', json={**CARD, 'credentials': {'cookie': 'replacement', 'csrf_token': 'new-token'}, 'counter': 1, 'kizu': 0}, headers=KEY)
    assert replay.status_code == first.status_code and replay.json() == first.json()
    assert replay.headers['idempotency-replayed'] == 'true'
    assert replay.headers['cache-control'] == 'no-store'
    assert sum(r.method == 'POST' for r in upstream[0]) == 1
    with app.state.auth.store.connect() as db:
        row = db.execute('SELECT * FROM cart_operations').fetchone()
        assert row['state'] == 'complete'
        assert SECRETS['cookie'] not in row['response_json'] and SECRETS['csrf_token'] not in row['response_json']


def test_key_conflict_and_user_isolation(client, upstream):
    assert client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY).status_code == 200
    conflict = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS, 'counter': 2}, headers=KEY)
    assert conflict.status_code == 409 and conflict.json()['detail']['type'] == 'idempotency_key_conflict'
    login(client, 'bob')
    assert client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY).status_code == 200
    assert sum(r.method == 'POST' for r in upstream[0]) == 2


def test_concurrent_duplicate_rejected_while_original_in_flight(client, monkeypatch):
    entered, release = Event(), Event()
    calls, added = [], False
    def handle(self, request):
        nonlocal added
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(200, text=cart_html(quantity=1) if added else EMPTY_CART)
        entered.set()
        assert release.wait(5)
        added = True
        return httpx.Response(200, json={'status': 'SUCCESS'})
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle)
    with ThreadPoolExecutor(max_workers=1) as pool:
        original = pool.submit(client.post, BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY)
        try:
            assert entered.wait(5)
            duplicate = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY)
            assert duplicate.status_code == 409
            assert duplicate.json()['detail']['type'] == 'cart_operation_pending'
        finally:
            release.set()
        assert original.result(timeout=5).status_code == 200
    assert client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY).status_code == 200
    assert [r.method for r in calls] == ['GET', 'POST', 'GET']


def test_crash_after_post_does_not_reissue_on_restart(client, upstream, monkeypatch):
    original = cart_operations.finish
    def crash(*args):
        raise RuntimeError('simulated crash before saving outcome')
    monkeypatch.setattr(cart_operations, 'finish', crash)
    with pytest.raises(RuntimeError, match='simulated crash'):
        client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY)
    monkeypatch.setattr(cart_operations, 'finish', original)
    with TestClient(app, base_url=ORIGIN, headers=dict(client.headers)) as restarted:
        response = restarted.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY)
    assert response.status_code == 409
    assert response.json()['detail'] == {'type': 'cart_operation_pending', 'outcome': 'unknown'}
    assert sum(r.method == 'POST' for r in upstream[0]) == 1


def test_durable_reservation_survives_restart_before_post(client, upstream):
    user = client.get('/api/auth/me').json()
    store = UserStore(app.state.auth.store.path)
    assert cart_operations.reserve(store, user['id'], KEY['Idempotency-Key'], CartInput(**CARD).model_dump()) is None
    with TestClient(app, base_url=ORIGIN, headers=dict(client.headers)) as restarted:
        assert restarted.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY).status_code == 409
    assert upstream[0] == []


@pytest.mark.parametrize('failure', ['timeout', 'rejected'])
def test_post_failure_is_replayed_without_retry(client, upstream, failure):
    if failure == 'timeout':
        upstream[1]['error'] = httpx.ReadTimeout('secret')
    else:
        upstream[1]['text'] = '{"status":"ERROR1"}'
    first = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY)
    replay = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers=KEY)
    assert replay.status_code == first.status_code and replay.json() == first.json()
    assert replay.headers['idempotency-replayed'] == 'true'
    assert sum(r.method == 'POST' for r in upstream[0]) == 1


@pytest.mark.parametrize('key', ['', 'with space', 'x' * 129])
def test_invalid_key_never_calls_upstream(client, upstream, key):
    assert client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers={'Idempotency-Key': key}).status_code == 422
    assert upstream[0] == []


def test_cors_allows_idempotency_header(client):
    response = client.options(BASE + '/cart', headers={'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'authorization,content-type,idempotency-key'})
    assert response.status_code == 200
    assert 'idempotency-key' in response.headers['access-control-allow-headers'].lower()


def test_v5_migration_removes_credentials(tmp_path):
    path = tmp_path / 'v5.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE users (id TEXT PRIMARY KEY);
            INSERT INTO users VALUES ('alice');
            CREATE TABLE yuyutei_credentials (user_id TEXT, session_cookie TEXT, csrf_token TEXT, updated_at INTEGER);
            INSERT INTO yuyutei_credentials VALUES ('alice','saved-cookie','saved-token',1);
            PRAGMA user_version=5;''')
    for _ in range(2):
        with UserStore(path).connect() as db:
            assert db.execute('PRAGMA user_version').fetchone()[0] == 8
            assert not db.execute("SELECT name FROM sqlite_master WHERE name='yuyutei_credentials'").fetchone()
            assert db.execute('SELECT count(*) FROM cart_operations').fetchone()[0] == 0
