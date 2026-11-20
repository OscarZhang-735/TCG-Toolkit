from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from auth_helpers import persisted_data, register_user
from tcg_toolkit.cart import yuyutei
from tcg_toolkit.web import app


ORIGIN = 'https://testserver'
BASE = '/api/yuyutei/cart'
CARD = {'gid': 56, 'ver': 'OS01', 'cid': '10044'}
SECRETS = {'cookie': 'alice-session%3D', 'csrf_token': 'alice-csrf'}
BODY = {**CARD, 'credentials': SECRETS}

CART_META = '<meta name="csrf-token" content="alice-csrf">'
EMPTY_CART = CART_META + '<div id="cart_all"><div id="cart_empty_meg">Empty</div></div>'
SUCCESS = (200, '{"status":"SUCCESS"}')


def cart_html(index=5, quantity=3):
    return CART_META + f'''<div id="cart_all"><div id="cart_empty_meg" style="display:none">Empty</div>
    <div id="itemInfo{index}">
      <a href="/sell/wsr/card/OS01/10044">Test card</a>
      <input id="itemValue{index}" value="{quantity}">
      <input id="itemPrice{index}" value="30">
      <input id="itemData{index}" value="56OS01100440">
      <a href="javascript:(removeItem('itemValue{index}', 'normal'))">Remove</a>
    </div></div>'''


def login(client, name):
    response = register_user(client, name)
    assert response.status_code == 201
    client.headers['Authorization'] = 'Bearer ' + response.json()['token']


@pytest.fixture
def upstream(monkeypatch):
    monkeypatch.setenv('TCG_SEARCH_PROXY_URL', '')
    calls, replies = [], []

    def handle(self, request):
        calls.append(request)
        assert replies, 'Unexpected upstream request'
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return httpx.Response(status, text=body)

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle)
    return calls, replies


@pytest.fixture
def client(postgres_schema, monkeypatch, upstream):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        login(client, 'alice')
        yield client


def test_add_verifies_quantity_and_keeps_credentials_request_scoped(client, upstream):
    calls, replies = upstream
    replies.extend([(200, EMPTY_CART), SUCCESS, (200, cart_html(quantity=2))])
    response = client.post(BASE, json={**BODY, 'counter': 2}, headers={'Idempotency-Key': 'add-two'})
    assert response.status_code == 200
    result = response.json()
    assert result['verification'] == 'verified' and result['observed_delta'] == 2
    assert result['cart']['total_quantity'] == 2 and result['cart']['subtotal'] == 60
    assert [r.method for r in calls] == ['GET', 'POST', 'GET']
    write = calls[1]
    assert str(write.url) == yuyutei.CART_URL
    assert write.headers['cookie'] == 'laravel_session=' + SECRETS['cookie']
    assert write.headers['x-csrf-token'] == SECRETS['csrf_token']
    assert 'authorization' not in write.headers
    assert parse_qs(write.content.decode())['counter'] == ['2']
    raw = persisted_data(app.state.database)
    assert all(secret.encode() not in raw for secret in SECRETS.values())

    client.cookies.set('laravel_session', SECRETS['cookie'])
    missing = client.post(BASE, json=CARD, params=SECRETS, headers={'Idempotency-Key': 'add-two'})
    assert missing.status_code == 422 and len(calls) == 3


def test_expired_shop_session_prevents_mutation(client, upstream):
    calls, replies = upstream
    replies.append((200, EMPTY_CART.replace('alice-csrf', 'guest-csrf')))
    response = client.post(BASE, json=BODY)
    assert response.status_code == 409
    assert response.json()['detail']['type'] == 'yuyutei_credentials_rejected'
    assert [r.method for r in calls] == ['GET']


def test_remove_uses_current_row_and_verifies_new_cart(client, upstream):
    calls, replies = upstream
    # The row was renumbered at the shop between the query and removal.
    replies.extend([(200, cart_html(index=5)), (200, cart_html(index=9)),
                    (200, '{"CART_ITEM_ID":"itemValue9","GAME_ID":"56","RESERVED_CHECK":0}'),
                    (200, EMPTY_CART)])
    query = client.post(BASE + '/query', json={'credentials': SECRETS})
    assert query.json()['items'][0]['cid'] == CARD['cid']
    assert query.json()['subtotal'] == 90
    removed = client.request('DELETE', BASE, json=BODY)
    assert removed.status_code == 200
    assert removed.json()['removed_quantity'] == 3 and removed.json()['cart']['items'] == []
    assert [r.method for r in calls] == ['GET', 'GET', 'POST', 'GET']
    assert parse_qs(calls[2].content.decode())['CART_ITEM_ID'] == ['itemValue9']


def test_incomplete_cart_has_no_totals_and_blocks_removal(client, upstream):
    calls, replies = upstream
    mixed = cart_html().rsplit('</div>', 1)[0] + '<div id="itemInfo6">Incomplete row</div></div>'
    replies.extend([(200, mixed), (200, mixed)])
    result = client.post(BASE + '/query', json={'credentials': SECRETS}).json()
    assert len(result['items']) == 1 and result['complete'] is False
    assert result['total_quantity'] is None and result['subtotal'] is None
    assert client.request('DELETE', BASE, json=BODY).status_code == 502
    assert [r.method for r in calls] == ['GET', 'GET']


def test_setting_quantity_twice_only_sends_one_decrement(client, upstream):
    calls, replies = upstream
    changed = (200, '{"CART_ITEM_ID":"itemValue5","cart_item_result":true}')
    replies.extend([(200, cart_html(quantity=3)), changed,
                    (200, cart_html(quantity=1)), (200, cart_html(quantity=1))])
    for _ in range(2):
        response = client.put(BASE + '/quantity', json={**BODY, 'quantity': 1})
        assert response.status_code == 200
        assert response.json()['cart']['total_quantity'] == 1
    assert [r.method for r in calls] == ['GET', 'POST', 'GET', 'GET']
    assert parse_qs(calls[1].content.decode())['counter'] == ['-2']


def test_concurrent_users_removing_from_same_shop_session_only_write_once(client, monkeypatch):
    alice = dict(client.headers)
    login(client, 'bob')
    entered, release, waiting = Event(), Event(), Event()
    calls = []
    original_lock = yuyutei.session_lock

    @contextmanager
    def observed_lock(identity):
        if entered.is_set():
            waiting.set()
        with original_lock(identity):
            yield

    def handle(self, request):
        calls.append(request.method)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            return httpx.Response(200, text=cart_html())
        if request.method == 'POST':
            return httpx.Response(200, json={'CART_ITEM_ID': 'itemValue5', 'GAME_ID': '56', 'RESERVED_CHECK': 0})
        return httpx.Response(200, text=EMPTY_CART)

    monkeypatch.setattr(yuyutei, 'session_lock', observed_lock)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.request, 'DELETE', BASE, json=BODY, headers=alice)
        try:
            assert entered.wait(5)
            # The shop can re-encrypt its session cookie while keeping the same CSRF token.
            second_body = {**BODY, 'credentials': {**SECRETS, 'cookie': 're-encrypted-cookie'}}
            second = pool.submit(client.request, 'DELETE', BASE, json=second_body)
            assert waiting.wait(5)
            assert calls == ['GET']
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
        assert second.result(timeout=5).status_code == 404
    assert calls == ['GET', 'POST', 'GET', 'GET']
