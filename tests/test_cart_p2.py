from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.cart import operations
from tcg_toolkit.web import app
from test_yuyutei_cart import BASE, BODY, EMPTY_CART, ORIGIN, SUCCESS, cart_html, client, login, upstream


KEY = {'Idempotency-Key': 'test-operation-1'}


def test_replay_after_restart_uses_original_result_with_new_credentials(client, upstream):
    calls, replies = upstream
    replies.extend([(200, EMPTY_CART), SUCCESS, (200, cart_html(quantity=1))])
    first = client.post(BASE, json=BODY, headers=KEY)
    assert first.status_code == 200
    replacement = {'cookie': 'replacement', 'csrf_token': 'new-token'}
    with TestClient(app, base_url=ORIGIN, headers=dict(client.headers)) as restarted:
        replay = restarted.post(BASE, json={**BODY, 'credentials': replacement, 'counter': 1, 'kizu': 0}, headers=KEY)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert replay.headers['idempotency-replayed'] == 'true'
    assert [r.method for r in calls] == ['GET', 'POST', 'GET']


def test_key_conflict_and_user_isolation(client, upstream):
    calls, replies = upstream
    replies.extend([(200, EMPTY_CART), SUCCESS, (200, cart_html(quantity=1))])
    assert client.post(BASE, json=BODY, headers=KEY).status_code == 200
    conflict = client.post(BASE, json={**BODY, 'counter': 2}, headers=KEY)
    assert conflict.status_code == 409 and conflict.json()['detail']['type'] == 'idempotency_key_conflict'
    login(client, 'bob')
    replies.extend([(200, cart_html(quantity=1)), SUCCESS, (200, cart_html(quantity=2))])
    assert client.post(BASE, json=BODY, headers=KEY).status_code == 200
    assert sum(r.method == 'POST' for r in calls) == 2


@pytest.mark.parametrize('after,expected,verification', [
    (httpx.ReadTimeout('upstream timeout'), 504, 'unknown'),
    ((200, EMPTY_CART), 409, 'mismatch'),
])
def test_unconfirmed_add_is_replayed_without_another_write(client, upstream, after, expected, verification):
    calls, replies = upstream
    replies.extend([(200, EMPTY_CART), SUCCESS, after])
    first = client.post(BASE, json=BODY, headers=KEY)
    assert first.status_code == expected
    assert first.json()['detail']['outcome'] == 'unknown'
    assert first.json()['detail']['verification'] == verification
    replay = client.post(BASE, json=BODY, headers=KEY)
    assert replay.status_code == expected and replay.json() == first.json()
    assert replay.headers['idempotency-replayed'] == 'true'
    assert [r.method for r in calls] == ['GET', 'POST', 'GET']


def test_concurrent_duplicate_rejected_while_original_in_flight(client, monkeypatch):
    entered, release = Event(), Event()
    calls = []

    def handle(self, request):
        calls.append(request.method)
        if request.method == 'GET':
            return httpx.Response(200, text=cart_html(quantity=1) if entered.is_set() else EMPTY_CART)
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json={'status': 'SUCCESS'})

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle)
    with ThreadPoolExecutor(max_workers=1) as pool:
        original = pool.submit(client.post, BASE, json=BODY, headers=KEY)
        try:
            assert entered.wait(5)
            duplicate = client.post(BASE, json=BODY, headers=KEY)
            assert duplicate.status_code == 409
            assert duplicate.json()['detail']['type'] == 'cart_operation_pending'
        finally:
            release.set()
        assert original.result(timeout=5).status_code == 200
    assert calls == ['GET', 'POST', 'GET']


def test_crash_after_post_does_not_reissue_on_restart(client, upstream, monkeypatch):
    calls, replies = upstream
    replies.extend([(200, EMPTY_CART), SUCCESS, (200, cart_html(quantity=1))])

    def crash(*args):
        raise RuntimeError('simulated crash before saving outcome')

    with monkeypatch.context() as patch:
        patch.setattr(operations, 'finish', crash)
        with pytest.raises(RuntimeError, match='simulated crash'):
            client.post(BASE, json=BODY, headers=KEY)
    with TestClient(app, base_url=ORIGIN, headers=dict(client.headers)) as restarted:
        response = restarted.post(BASE, json=BODY, headers=KEY)
    assert response.status_code == 409
    assert response.json()['detail'] == {'type': 'cart_operation_pending', 'outcome': 'unknown'}
    assert [r.method for r in calls] == ['GET', 'POST', 'GET']
