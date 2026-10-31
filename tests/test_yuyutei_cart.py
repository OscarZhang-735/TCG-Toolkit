from auth_helpers import register_user
from datetime import datetime
import json
import sqlite3
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.web import app
from tcg_toolkit.yuyutei_cart import CART_URL

ORIGIN = 'https://testserver'
BASE = '/api/yuyutei'
CARD = {'gid': 56, 'ver': 'OS01', 'cid': '10044'}
SECRETS = {'cookie': 'alice-session%3D', 'csrf_token': 'alice-csrf'}


def login(client, name):
    response = register_user(client, name)
    assert response.status_code == 201
    client.headers['Authorization'] = 'Bearer ' + response.json()['token']


@pytest.fixture
def upstream(monkeypatch):
    monkeypatch.setenv('TCG_SEARCH_PROXY_URL', '')
    calls = []
    result = {'status': 200, 'text': '{"status":"SUCCESS"}'}

    carts = {}
    def handle(self, request):
        calls.append(request)
        cookie = request.headers.get('cookie', '')
        if request.method == 'GET':
            if 'page' in result:
                return httpx.Response(result.get('page_status', 200), text=result['page'])
            token = {'laravel_session=alice-session%3D': 'alice-csrf',
                     'laravel_session=bob-session': 'bob-csrf',
                     'laravel_session=replacement': 'new-token'}.get(cookie, 'alice-csrf')
            if cookie in carts:
                body, qty = carts[cookie]
                page = cart_html(quantity=qty, gid=int(body['gid']), ver=body['ver'],
                                 cid=body['cid'], kizu=int(body['kizu']))
            else:
                page = EMPTY_CART
            return httpx.Response(200, text=page.replace('alice-csrf', token))
        if 'error' in result:
            raise result['error']
        if result['status'] == 200 and result['text'] == '{"status":"SUCCESS"}':
            body = {k:v[0] for k,v in parse_qs(request.content.decode()).items()}
            previous = carts.get(cookie, (None, 0))[1]
            carts[cookie] = (body, previous + int(body['counter']))
        return httpx.Response(result['status'], text=result['text'],
                              headers={'Content-Type': 'text/html', 'Location': 'https://other.example/'})

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle)
    return calls, result


@pytest.fixture
def client(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv('TCG_USER_DB', str(tmp_path / 'users.sqlite3'))
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        login(client, 'alice')
        yield client


def test_credentials_are_request_scoped_and_never_stored(client, upstream):
    for secrets in [SECRETS, {'cookie': 'replacement', 'csrf_token': 'new-token'}]:
        response = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS, 'credentials': secrets})
        assert response.status_code == 200
        post = [r for r in upstream[0] if r.method == 'POST'][-1]
        assert post.headers['cookie'] == 'laravel_session=' + secrets['cookie']
        assert post.headers['x-csrf-token'] == secrets['csrf_token']
    calls = len(upstream[0])
    assert client.post(BASE + '/cart', json=CARD).status_code == 422
    assert len(upstream[0]) == calls
    with UserStore(app.state.auth.store.path).connect() as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='yuyutei_credentials'").fetchone()
    raw = app.state.auth.store.path.read_bytes()
    for secret in SECRETS.values():
        assert secret.encode() not in raw


def test_add_uses_supplied_credentials_and_retains_protocol(client, upstream):
    response = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS, 'counter': 2, 'credentials': SECRETS})
    result = response.json()
    cart = result.pop('cart')
    assert cart['total_quantity'] == 2 and cart['subtotal'] == 60
    assert cart['complete'] is True and cart['warnings'] == []
    assert '_cart_item_id' not in cart['items'][0]
    assert result == {'status': 'SUCCESS', **CARD, 'counter': 2, 'kizu': 0,
        'requested_quantity': 2, 'before_quantity': 0, 'after_quantity': 2, 'observed_delta': 2, 'verification': 'verified'}
    request = [r for r in upstream[0] if r.method == 'POST'][-1]
    assert request.method == 'POST' and str(request.url) == CART_URL
    assert request.headers['cookie'] == 'laravel_session=alice-session%3D'
    assert request.headers['x-csrf-token'] == 'alice-csrf'
    assert request.headers['referer'] == 'https://yuyu-tei.jp/sell/wsr/card/OS01/10044'
    assert request.headers['content-type'] == 'application/x-www-form-urlencoded'
    assert 'authorization' not in request.headers
    payload = parse_qs(request.content.decode())
    timestamp = payload.pop('time')[0]
    assert datetime.fromisoformat(timestamp).utcoffset().total_seconds() == 0
    assert payload == {k: [str(v)] for k, v in {**CARD, 'counter': 2, 'kizu': 0, 'mode': 'add', 'type': 'sell'}.items()}
    assert request.extensions['timeout']['read'] == 20


@pytest.mark.parametrize('gid,path', [(7, 'ws'), (8, 'chaos'), (11, 'pm'), (34, 'lo'), (47, 'sev'), (56, 'wsr')])
def test_game_referers(client, upstream, gid, path):
    response = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS, 'gid': gid, 'ver': 'bd5.0', 'kizu': 1})
    assert response.status_code == 200
    assert [r for r in upstream[0] if r.method == 'POST'][-1].headers['referer'] == f'https://yuyu-tei.jp/sell/{path}/card/bd5.0/10044'
    assert parse_qs([r for r in upstream[0] if r.method == 'POST'][-1].content.decode())['kizu'] == ['1']


@pytest.mark.parametrize('method,path', [('POST', '/cart/query'), ('DELETE', '/cart'), ('POST', '/cart')])
def test_auth_origin_content_type_and_no_store(client, upstream, method, path):
    body = {'credentials': SECRETS, **(CARD if path == '/cart' else {})}
    if method != 'GET':
        for headers, expected in [({'Origin': 'https://evil.example'}, 403),
                                  ({'Content-Type': 'text/plain'}, 415)]:
            response = client.request(method, BASE + path, json=body, headers=headers)
            assert response.status_code == expected
            assert response.headers['cache-control'] == 'no-store'
    client.headers.pop('Authorization')
    response = client.request(method, BASE + path, json=body)
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'
    assert upstream[0] == []


@pytest.mark.parametrize('method,path', [('POST', '/cart/query'), ('POST', '/cart'), ('DELETE', '/cart')])
@pytest.mark.parametrize('invalid', [None, {}, {'cookie': 'private-secret'},
    {**SECRETS, 'cookie': ''}, {**SECRETS, 'cookie': 'private-secret\r\nInjected: x'},
    {**SECRETS, 'cookie': 'laravel_session=a; laravel_session=b'},
    {**SECRETS, 'csrf_token': ['private-secret']}, {**SECRETS, 'cookie': 'x' * 16385}])
def test_secret_validation_does_not_echo_input(client, upstream, method, path, invalid):
    body = {'credentials': invalid, **(CARD if path == '/cart' else {})}
    response = client.request(method, BASE + path, json=body)
    assert response.status_code == 422
    assert response.json() == {'detail': 'invalid_yuyutei_request'}
    assert not upstream[0]


@pytest.mark.parametrize('method,path', [('POST', '/cart/query'), ('POST', '/cart'), ('DELETE', '/cart')])
def test_malformed_json_does_not_echo_secrets(client, upstream, method, path):
    response = client.request(method, BASE + path, content='{"credentials":{"cookie":"private-secret"},broken',
                              headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert response.json() == {'detail': 'invalid_yuyutei_request'}
    assert not upstream[0]


@pytest.mark.parametrize('field,value', [('gid', 999), ('ver', '../x'), ('ver', ''),
    ('ver', 'x?foo'), ('cid', '12/34'), ('cid', ''), ('counter', 0), ('counter', 100),
    ('counter', True), ('counter', '1'), ('counter', 1.5), ('kizu', 2), ('kizu', True),
    ('mode', 'delete'), ('user_id', 'other'), ('referer', 'https://evil.example')])
def test_cart_validation_never_calls_shop(client, upstream, field, value):
    response = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS, field: value})
    assert response.status_code == 422
    assert response.headers['cache-control'] == 'no-store'
    assert upstream[0] == []


@pytest.mark.parametrize('http_status,body,expected,error', [
    (419, 'secret-cookie', 409, 'yuyutei_credentials_rejected'),
    (403, 'secret-cookie', 409, 'yuyutei_credentials_rejected'),
    (302, '', 502, 'yuyutei_unavailable'),
    (500, 'secret-cookie', 502, 'yuyutei_unavailable'),
    (200, '{"status":"ERROR1"}', 409, 'yuyutei_cart_rejected'),
    (200, '{"status":"ERROR3"}', 409, 'yuyutei_cart_rejected'),
    (200, '<html>secret-cookie</html>', 502, 'yuyutei_invalid_response'),
    (200, '[]', 502, 'yuyutei_invalid_response'),
    (200, '{"status":"secret-cookie"}', 502, 'yuyutei_invalid_response'),
    (200, '{"status":true}', 502, 'yuyutei_invalid_response')])
def test_upstream_errors_not_success_or_retried(client, upstream, http_status, body, expected, error):
    upstream[1].update(status=http_status, text=body)
    response = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS})
    assert response.status_code == expected
    assert response.json()['detail']['type'] == error
    assert 'secret-cookie' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert sum(r.method == 'POST' for r in upstream[0]) == 1
    if error == 'yuyutei_cart_rejected':
        assert response.json()['detail']['code'] == json.loads(body)['status']


@pytest.mark.parametrize('exception,status', [(httpx.ReadTimeout('secret-cookie'), 504),
                                            (httpx.ConnectError('secret-cookie'), 502)])
def test_uncertain_transport_outcome_not_retried(client, upstream, exception, status):
    upstream[1]['error'] = exception
    response = client.post(BASE + '/cart', json={**CARD, 'credentials': SECRETS})
    assert response.status_code == status
    assert response.json()['detail']['outcome'] == 'unknown'
    assert 'secret-cookie' not in response.text
    assert sum(r.method == 'POST' for r in upstream[0]) == 1


def test_v4_migration_preserves_existing_tables(tmp_path):
    path = tmp_path / 'v4.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE users (id TEXT PRIMARY KEY);
            INSERT INTO users VALUES ('alice');
            CREATE TABLE sessions (token_hash TEXT);
            INSERT INTO sessions VALUES ('existing-session');
            CREATE TABLE favorites (id TEXT);
            INSERT INTO favorites VALUES ('existing-favorite');
            CREATE TABLE owned_cards (id TEXT, image_url TEXT);
            INSERT INTO owned_cards VALUES ('existing-card', 'https://example.com/card.jpg');
            PRAGMA user_version=4;''')
    for _ in range(2):
        with UserStore(path).connect() as db:
            assert db.execute('PRAGMA user_version').fetchone()[0] == 8
            assert db.execute('SELECT id FROM users').fetchone()[0] == 'alice'
            assert db.execute('SELECT token_hash FROM sessions').fetchone()[0] == 'existing-session'
            assert db.execute('SELECT id FROM favorites').fetchone()[0] == 'existing-favorite'
            assert tuple(db.execute('SELECT * FROM owned_cards').fetchone()) == ('existing-card', 'https://example.com/card.jpg')
            assert not db.execute("SELECT name FROM sqlite_master WHERE name='yuyutei_credentials'").fetchone()


CART_META = '<meta name="csrf-token" content="alice-csrf">'
EMPTY_CART = CART_META + '<div id="cart_all"><div id="cart_empty_meg">Empty</div></div>'


def cart_html(index=5, quantity=3, kizu=0, kind='normal', gid=56, ver='OS01', cid='10044'):
    from tcg_toolkit.yuyutei_cart import GAME_PATHS
    game = GAME_PATHS[gid]
    prefix = 'itemReservedInfo' if kind == 'reserved' else 'itemInfo'
    return CART_META + f'''<div id="cart_all"><div id="cart_empty_meg" style="display:none">Empty</div>
    <div id="{prefix}{index}">
      <a href="/sell/{game}/card/{ver}/{cid}"><img src="x"></a>
      <a href="/sell/{game}/card/{ver}/{cid}">C Card</a>
      <input id="itemValue{index}" value="{quantity}">
      <input id="itemPrice{index}" value="30">
      <input id="itemData{index}" value="{gid}{ver}{cid}{kizu}">
      <a href="javascript:(removeItem('itemValue{index}', '{kind}'))">Remove</a>
    </div></div>'''


def cart_sequence(monkeypatch, responses):
    calls = []
    def handle(self, request):
        calls.append(request)
        assert responses, 'Unexpected upstream call / retry'
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return httpx.Response(value[0], text=value[1])
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle)
    return calls


def test_cart_query(client, upstream):
    upstream[1]['page'] = cart_html()
    response = client.post(BASE + '/cart/query', json={'credentials': SECRETS})
    assert response.status_code == 200
    result = response.json()
    assert result['total_quantity'] == 3 and result['subtotal'] == 90
    assert result['items'][0]['quantity'] == 3
    assert result['items'][0]['kizu'] == 0
    assert '_cart_item_id' not in response.text and 'itemValue' not in response.text
    assert upstream[0][0].headers['cookie'] == 'laravel_session=alice-session%3D'
    assert response.headers['cache-control'] == 'no-store'
    upstream[1]['page'] = EMPTY_CART
    assert client.post(BASE + '/cart/query', json={'credentials': SECRETS}).json()['items'] == []


@pytest.mark.parametrize('kind,kizu', [('normal', 0), ('normal', 1), ('reserved', 0)])
def test_remove_resolves_current_id_and_checks_after(client, monkeypatch, kind, kizu):
    # Index changes between the earlier listing and the deletion's fresh GET.
    calls = cart_sequence(monkeypatch, [(200, cart_html(5, kizu=kizu, kind=kind)),
        (200, cart_html(9, kizu=kizu, kind=kind)),
        (200, '{"CART_ITEM_ID":"itemValue9","GAME_ID":"56","RESERVED_CHECK":0}'),
        (200, EMPTY_CART)])
    assert client.post(BASE + '/cart/query', json={'credentials': SECRETS}).status_code == 200
    response = client.request('DELETE', BASE + '/cart', json={**CARD, 'credentials': SECRETS, 'kizu': kizu, 'type': kind})
    assert response.status_code == 200
    assert response.json()['removed_quantity'] == 3
    assert response.json()['cart']['total_quantity'] == 0
    assert [r.method for r in calls] == ['GET', 'GET', 'POST', 'GET']
    assert str(calls[2].url) == 'https://yuyu-tei.jp/api/cart_service'
    assert parse_qs(calls[2].content.decode()) == {'mode':['remove'], 'CART_ITEM_ID':['itemValue9'], 'status':['sell'], 'type':[kind]}
    assert calls[2].headers['x-csrf-token'] == SECRETS['csrf_token']
    assert calls[2].headers['referer'] == 'https://yuyu-tei.jp/cart/sell'


@pytest.mark.parametrize('page,status', [(EMPTY_CART, 404), (cart_html(kizu=1), 404),
    ('<html>login</html>', 502), (cart_html().replace('itemData5', 'missing'), 502),
    (cart_html().replace('value="3"', 'value="oops"'), 502),
    (cart_html().replace('removeItem(', 'other('), 502),
    (cart_html().replace('</div></div>', '</div>' + cart_html(6).replace(CART_META, '') + '</div>'), 409)])
def test_remove_will_not_send_when_target_unresolved(client, upstream, page, status):
    upstream[1]['page'] = page
    response = client.request('DELETE', BASE + '/cart', json={**CARD, 'credentials': SECRETS})
    assert response.status_code == status
    assert len(upstream[0]) == 1 and upstream[0][0].method == 'GET'


@pytest.mark.parametrize('reply,after,status', [
    ((419, 'secret'), None, 409), ((302, 'secret'), None, 502),
    ((200, '{}'), None, 502), ((200, 'secret'), None, 502),
    (httpx.ReadTimeout('secret'), None, 504),
    ((200, '{"CART_ITEM_ID":"itemValue5","GAME_ID":"56","RESERVED_CHECK":0}'), cart_html(), 409),
    ((200, '{"CART_ITEM_ID":"itemValue5","GAME_ID":"56","RESERVED_CHECK":0}'), '<html>bad</html>', 502)])
def test_remove_failure_and_uncertain_results(client, monkeypatch, reply, after, status):
    responses = [(200, cart_html()), reply]
    if after is not None:
        responses.append((200, after))
    calls = cart_sequence(monkeypatch, responses)
    response = client.request('DELETE', BASE + '/cart', json={**CARD, 'credentials': SECRETS})
    assert response.status_code == status
    assert 'secret' not in response.text
    assert sum(r.method == 'POST' for r in calls) == 1


@pytest.mark.parametrize('method,path', [('POST', '/cart/query'), ('DELETE', '/cart')])
def test_cart_read_remove_isolation_guards(client, upstream, method, path):
    body = {'credentials': SECRETS, **(CARD if path == '/cart' else {})}
    assert client.request(method, BASE + path, json=body, headers={'Origin':'https://evil.test'}).status_code == 403
    assert client.request(method, BASE + path, content='{}', headers={'Content-Type':'text/plain'}).status_code == 415
    assert client.request(method, BASE + path, json={**body, 'CART_ITEM_ID':'itemValue5'}).status_code == 422
    client.headers.pop('Authorization')
    response = client.request(method, BASE + path, json=body)
    assert response.status_code == 401 and response.headers['cache-control'] == 'no-store'
    assert not upstream[0]


@pytest.mark.parametrize('method', ['GET', 'DELETE'])
@pytest.mark.parametrize('page', [EMPTY_CART, cart_html()])
def test_changed_session_is_rejected_before_any_mutation(client, upstream, method, page):
    upstream[1]['page'] = page.replace('alice-csrf', 'new-anonymous-csrf')
    response = client.request('POST' if method == 'GET' else method, BASE + ('/cart/query' if method == 'GET' else '/cart'), json={'credentials': SECRETS, **(CARD if method != 'GET' else {})})
    assert response.status_code == 409
    assert response.json()['detail']['type'] == 'yuyutei_credentials_rejected'
    assert 'items' not in response.json()
    assert [r.method for r in upstream[0]] == ['GET']


@pytest.mark.parametrize('page', [EMPTY_CART.replace(CART_META, ''),
                                  EMPTY_CART.replace('alice-csrf', ''), CART_META + EMPTY_CART])
def test_missing_or_ambiguous_csrf_is_not_empty_cart(client, upstream, page):
    upstream[1]['page'] = page
    assert client.post(BASE + '/cart/query', json={'credentials': SECRETS}).status_code == 502


def test_session_expires_after_remove_cannot_claim_success(client, monkeypatch):
    calls = cart_sequence(monkeypatch, [(200, cart_html()),
        (200, '{"CART_ITEM_ID":"itemValue5","GAME_ID":"56","RESERVED_CHECK":0}'),
        (200, EMPTY_CART.replace('alice-csrf', 'new-anonymous-csrf'))])
    response = client.request('DELETE', BASE + '/cart', json={**CARD, 'credentials': SECRETS})
    assert response.status_code == 409
    assert response.json()['detail'] == {'type': 'yuyutei_credentials_rejected', 'outcome': 'unknown'}
    assert [r.method for r in calls] == ['GET', 'POST', 'GET']


@pytest.mark.parametrize('second_method,expected', [('GET', 200), ('POST', 200), ('DELETE', 404)])
def test_same_upstream_session_serializes_entire_remove_across_users(client, monkeypatch, second_method, expected):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event, Lock
    import tcg_toolkit.yuyutei_cart as module
    alice = client.headers['Authorization']
    login(client, 'bob')
    # The same Laravel session may have differently encrypted cookie strings.
    bob = client.headers['Authorization']
    entered_get, release_get, second_waiting = Event(), Event(), Event()
    guard = Lock()
    attempts = 0
    original = module.session_lock
    @contextmanager
    def observed(token):
        nonlocal attempts
        with guard:
            attempts += 1
            if attempts == 2:
                second_waiting.set()
        with original(token):
            yield
    monkeypatch.setattr(module, 'session_lock', observed)
    calls = []
    present = True
    quantity = 3
    def handle(self, request):
        nonlocal present, quantity
        calls.append(request)
        if len(calls) == 1:
            entered_get.set()
            assert release_get.wait(5)
        if request.method == 'GET':
            return httpx.Response(200, text=cart_html(quantity=quantity) if present else EMPTY_CART)
        if str(request.url).endswith('/cart_service'):
            present = False
            return httpx.Response(200, json={'CART_ITEM_ID':'itemValue5','GAME_ID':'56','RESERVED_CHECK':0})
        present, quantity = True, 1
        return httpx.Response(200, json={'status':'SUCCESS'})
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.request, 'DELETE', BASE + '/cart', json={**CARD, 'credentials': SECRETS}, headers={'Authorization':alice})
        try:
            assert entered_get.wait(5)
            second = pool.submit(client.request, 'POST' if second_method == 'GET' else second_method, BASE + ('/cart/query' if second_method == 'GET' else '/cart'), json={'credentials': {'cookie': 're-encrypted-cookie', 'csrf_token': 'alice-csrf'}, **(CARD if second_method != 'GET' else {})}, headers={'Authorization':bob})
            assert second_waiting.wait(5)
            assert len(calls) == 1  # The second route reached the lock but not the shop.
        finally:
            release_get.set()
        assert first.result(timeout=5).status_code == 200
        assert second.result(timeout=5).status_code == expected
    assert [r.method for r in calls[:3]] == ['GET', 'POST', 'GET']
    assert sum(str(r.url).endswith('/cart_service') for r in calls) == 1
    assert module._session_locks == {}


def test_session_lock_does_not_block_other_sessions_and_releases_on_error():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from tcg_toolkit.yuyutei_cart import session_lock, _session_locks
    entered = Event()
    def other():
        with session_lock('different-session'):
            entered.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError):
            with session_lock('first-session'):
                future = pool.submit(other)
                assert entered.wait(5)
                raise RuntimeError('simulated failure')
        future.result(timeout=5)
    with session_lock('first-session'):
        pass
    assert _session_locks == {}


QTY = BASE + '/cart/quantity'
REMOVED = '{"CART_ITEM_ID":"itemValue5","GAME_ID":"56","RESERVED_CHECK":0}'


CHANGED = '{"CART_ITEM_ID":"itemValue5","cart_item_result":true}'


@pytest.mark.parametrize('quantity', [1, 2])
@pytest.mark.parametrize('kind', ['normal', 'reserved'])
def test_lower_quantity_sends_negative_difference(client, monkeypatch, quantity, kind):
    calls = cart_sequence(monkeypatch, [(200, cart_html(quantity=3, kind=kind)), (200, CHANGED),
                                        (200, cart_html(7, quantity=quantity, kind=kind))])
    response = client.put(QTY, json={**CARD, 'type': kind, 'quantity': quantity, 'credentials': SECRETS})
    assert response.status_code == 200
    result = response.json()
    assert (result['before_quantity'], result['after_quantity'], result['cart']['total_quantity']) == (3, quantity, quantity)
    assert [r.method for r in calls] == ['GET', 'POST', 'GET']
    assert str(calls[1].url).endswith('/cart_service')
    payload = parse_qs(calls[1].content.decode(), keep_blank_values=True)
    assert payload == {'mode': ['add'], 'CART_ITEM_ID': ['itemValue5'],
                       'counter': [str(quantity - 3)], 'status': ['sell'],
                       'type': ['' if kind == 'normal' else kind]}
    assert calls[1].headers['x-csrf-token'] == SECRETS['csrf_token']


def test_lower_quantity_to_zero_only_removes(client, monkeypatch):
    calls = cart_sequence(monkeypatch, [(200, cart_html(quantity=1)), (200, REMOVED), (200, EMPTY_CART)])
    response = client.put(QTY, json={**CARD, 'quantity': 0, 'credentials': SECRETS})
    assert response.status_code == 200 and response.json()['after_quantity'] == 0
    assert [r.method for r in calls] == ['GET', 'POST', 'GET']


@pytest.mark.parametrize('quantity,status', [(3, 200), (4, 409)])
def test_same_or_higher_target_never_mutates(client, monkeypatch, quantity, status):
    calls = cart_sequence(monkeypatch, [(200, cart_html(quantity=3))])
    response = client.put(QTY, json={**CARD, 'quantity': quantity, 'credentials': SECRETS})
    assert response.status_code == status
    assert [r.method for r in calls] == ['GET']


@pytest.mark.parametrize('reply', [(200, 'bad-json'), (200, '{}'),
    (200, '{"CART_ITEM_ID":"itemValue5","cart_item_result":false}'),
    (200, '{"CART_ITEM_ID":"itemValue6","cart_item_result":true}'),
    (419, ''), httpx.ReadTimeout('timeout')])
def test_quantity_failure_never_retries_or_readds(client, monkeypatch, reply):
    calls = cart_sequence(monkeypatch, [(200, cart_html(quantity=3)), reply])
    response = client.put(QTY, json={**CARD, 'quantity': 2, 'credentials': SECRETS})
    assert response.status_code in (409, 502, 504)
    detail = response.json()['detail']
    assert detail['outcome'] == 'unknown' and detail['after_quantity'] is None
    assert [r.method for r in calls] == ['GET', 'POST']


@pytest.mark.parametrize('page,status,count', [(cart_html(quantity=3), 409, 3),
    (EMPTY_CART, 409, 0), ('broken', 502, None),
    (EMPTY_CART.replace('alice-csrf', 'expired'), 409, None)])
def test_quantity_verifies_actual_cart(client, monkeypatch, page, status, count):
    calls = cart_sequence(monkeypatch, [(200, cart_html(quantity=3)), (200, CHANGED), (200, page)])
    response = client.put(QTY, json={**CARD, 'quantity': 2, 'credentials': SECRETS})
    assert response.status_code == status
    assert response.json()['detail']['outcome'] == 'unknown'
    assert response.json()['detail']['after_quantity'] == count
    assert len(calls) == 3


def test_repeating_target_does_not_decrement_again(client, monkeypatch):
    calls = cart_sequence(monkeypatch, [(200, cart_html(quantity=3)), (200, CHANGED),
        (200, cart_html(quantity=2)), (200, cart_html(quantity=2))])
    for _ in range(2):
        response = client.put(QTY, json={**CARD, 'quantity': 2, 'credentials': SECRETS})
        assert response.status_code == 200
    assert [r.method for r in calls] == ['GET', 'POST', 'GET', 'GET']


def test_quantity_verification_separates_reserved_row(client, monkeypatch):
    def page(quantity):
        reserved = cart_html(8, quantity=7, kind='reserved').split('<div id="itemReservedInfo8">')[1].rsplit('</div>', 1)[0]
        return cart_html(quantity=quantity).rsplit('</div>', 1)[0] + '<div id="itemReservedInfo8">' + reserved + '</div>'
    cart_sequence(monkeypatch, [(200, page(3)), (200, CHANGED), (200, page(2))])
    response = client.put(QTY, json={**CARD, 'quantity': 2, 'credentials': SECRETS})
    assert response.status_code == 200
    assert response.json()['after_quantity'] == 2
    assert response.json()['cart']['total_quantity'] == 9


@pytest.mark.parametrize('field,value', [('quantity', -1), ('quantity', 99), ('quantity', '1'),
                                         ('quantity', None), ('type', 'other'), ('counter', 1)])
def test_quantity_validation_never_calls_shop(client, upstream, field, value):
    response = client.put(QTY, json={**CARD, 'quantity': 1, 'credentials': SECRETS, field: value})
    assert response.status_code == 422 and response.json() == {'detail': 'invalid_yuyutei_request'}
    assert upstream[0] == []


@pytest.mark.parametrize('page', [EMPTY_CART, cart_html(cid='99999'), cart_html(kind='reserved')])
def test_zero_missing_returns_current_cart_without_write(client, monkeypatch, page):
    calls = cart_sequence(monkeypatch, [(200, page)])
    response = client.put(QTY, json={**CARD, 'quantity': 0, 'credentials': SECRETS})
    assert response.status_code == 200
    result = response.json()
    assert result['before_quantity'] == result['after_quantity'] == 0
    assert result['cart']['complete'] is True
    assert result['cart']['total_quantity'] == (0 if page == EMPTY_CART else 3)
    assert [r.method for r in calls] == ['GET']


def test_zero_repeated_after_removal_is_success(client, monkeypatch):
    calls = cart_sequence(monkeypatch, [(200, cart_html()), (200, REMOVED), (200, EMPTY_CART), (200, EMPTY_CART)])
    for _ in range(2):
        response = client.put(QTY, json={**CARD, 'quantity': 0, 'credentials': SECRETS})
        assert response.status_code == 200
        assert response.json()['cart']['items'] == []
    assert [r.method for r in calls] == ['GET', 'POST', 'GET', 'GET']


@pytest.mark.parametrize('method,body', [('PUT', {'quantity': 1}), ('DELETE', {})])
def test_missing_positive_target_and_delete_still_404(client, monkeypatch, method, body):
    calls = cart_sequence(monkeypatch, [(200, EMPTY_CART)])
    response = client.request(method, QTY if method == 'PUT' else BASE + '/cart',
        json={**CARD, **body, 'credentials': SECRETS})
    assert response.status_code == 404
    assert len(calls) == 1


@pytest.mark.parametrize('page,status', [('broken', 502), (EMPTY_CART.replace('alice-csrf', 'expired'), 409),
    (cart_html().replace('value="3"', 'value="invalid"'), 502),
    (cart_html().replace('</div></div>', '</div><div id="itemInfo6">' + cart_html(6).split('<div id="itemInfo6">')[1]), 409)])
def test_zero_does_not_hide_unreadable_or_ambiguous_cart(client, monkeypatch, page, status):
    calls = cart_sequence(monkeypatch, [(200, page)])
    response = client.put(QTY, json={**CARD, 'quantity': 0, 'credentials': SECRETS})
    assert response.status_code == status
    assert len(calls) == 1


def test_add_returns_verified_snapshot_and_replays_it_without_upstream(client, monkeypatch):
    calls = cart_sequence(monkeypatch, [(200, EMPTY_CART), (200, '{"status":"SUCCESS"}'), (200, cart_html(quantity=1))])
    body = {**CARD, 'credentials': SECRETS}
    headers = {'Idempotency-Key': 'snapshot-replay'}
    first = client.post(BASE + '/cart', json=body, headers=headers)
    assert first.status_code == 200
    assert first.json()['cart']['total_quantity'] == 1
    assert first.json()['cart']['items'][0]['cid'] == CARD['cid']
    replay = client.post(BASE + '/cart', json=body, headers=headers)
    assert replay.json() == first.json()
    assert replay.headers['idempotency-replayed'] == 'true'
    assert [r.method for r in calls] == ['GET', 'POST', 'GET']


@pytest.mark.parametrize('img,expected', [('<img src="/img/a.jpg">', 'https://yuyu-tei.jp/img/a.jpg'),
    ('<img data-src="https://card.yuyu-tei.jp/b.jpg">', 'https://card.yuyu-tei.jp/b.jpg'),
    ('<img src="javascript:alert(1)">', None), ('', None)])
def test_cart_query_thumbnail_is_optional(client, upstream, img, expected):
    upstream[1]['page'] = cart_html().replace('<img src="x">', img)
    response = client.post(BASE + '/cart/query', json={'credentials': SECRETS})
    assert response.status_code == 200
    assert response.json()['items'][0]['image_url'] == expected
