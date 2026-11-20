from urllib.parse import parse_qs

import httpx
import pytest

from tcg_toolkit.cart.hobbystation import BASE, HOST
from test_yuyutei_cart import client, upstream


API = '/api/hobbystation/cart'
URL = BASE + '/lo/product/detail/305273'
CREDS = {'eccube': 'test-session', 'eccube_bot_check': 'test-access'}
BODY = {'credentials': CREDS}
SKU = {'product_class_id': '304858'}


def link(path, method):
    return f'<a href="{path}" data-method="{method}" token-for-anchor="row-token">action</a>'


def cart_html(count):
    if not count:
        content = '<div class="ec-alert-warning__text">現在カート内に商品はございません。</div>'
    else:
        content = f'''<div class="ec-cartRow">
          <div class="ec-cartRow__delColumn">{link('/cart/remove/304858', 'put')}</div>
          <div class="ec-cartRow__name"><a href="{URL}">Test card</a></div>
          <div class="ec-cartRow__unitPrice">1,200円</div>
          <div class="ec-cartRow__amount">{count}</div>
          {link('/cart/up/304858', 'put')}{link('/cart/down/304858', 'put')}
        </div>{link('/cart/clear', 'post')}'''
    return '<main class="ec-cartRole">' + content + '</main>'


@pytest.fixture
def shop(client, monkeypatch):
    state = {'quantity': 0, 'writes': [], 'calls': []}

    def handle(self, req):
        assert req.url.host == HOST and 'authorization' not in req.headers
        path = req.url.path
        state['calls'].append(path)
        cookie = req.headers['Cookie']
        if str(req.url) == URL:
            if cookie == 'eccube=test-session':
                return httpx.Response(200, text='<title>安全な接続を確認中...</title>')
            assert cookie == 'eccube=test-session; eccube_bot_check=test-access'
            return httpx.Response(200, text='''<form action="/products/add_cart/305273" method="post">
                <input name="product_id" value="305273"><input name="ProductClass" value="304858">
                <input name="_token" value="form-token"></form>''')
        assert cookie == 'eccube=test-session'
        if path == '/cart':
            assert req.method == 'GET'
            if state.get('fail_read_at') == state['quantity']:
                return httpx.Response(200, text='service unavailable')
            return httpx.Response(200, text=state.get('html', cart_html(state['quantity'])),
                                  headers=state.get('headers', {}))
        assert req.method == 'POST'
        state['writes'].append(path)
        data = parse_qs(req.content.decode())
        if path == '/products/add_cart/305273':
            assert data == {'_token': ['form-token'], 'quantity': ['2'],
                            'product_id': ['305273'], 'ProductClass': ['304858']}
            assert req.headers['X-Requested-With'] == 'XMLHttpRequest'
            if 'add_reply' in state:
                return httpx.Response(200, json=state['add_reply'])
            state['quantity'] += 2
            return httpx.Response(200, json={'done': True})
        assert data == {'_method': ['post' if path == '/cart/clear' else 'put'], '_token': ['row-token']}
        if 'redirect' not in state and not state.get('ignore_write'):
            if path == '/cart/up/304858':
                state['quantity'] += 1
            elif path == '/cart/down/304858':
                state['quantity'] -= 1
            else:
                assert path in ('/cart/remove/304858', '/cart/clear')
                state['quantity'] = 0
        return httpx.Response(302, headers={'Location': state.get('redirect', '/cart')})

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle)
    return state


def test_add_query_quantity_remove_and_clear(client, shop):
    add = {**BODY, 'product_url': URL, 'counter': 2}
    headers = {'Idempotency-Key': 'test-hs-add'}
    response = client.post(API, json=add, headers=headers)
    assert response.status_code == 200
    assert response.json()['cart']['subtotal'] == 2400
    assert client.post(API, json=add, headers=headers).headers['Idempotency-Replayed'] == 'true'
    assert shop['writes'] == ['/products/add_cart/305273']
    creds = {'credentials': {'eccube': 'test-session'}}
    query = client.post(API + '/query', json=creds)
    assert query.headers['cache-control'] == 'no-store'
    assert query.json()['items'][0]['product_class_id'] == '304858'
    assert 'row-token' not in query.text and 'test-session' not in response.text
    row = {**creds, **SKU}
    for target in (3, 3, 1):
        result = client.put(API + '/quantity', json={**row, 'quantity': target})
        assert result.json()['cart']['total_quantity'] == target
    assert shop['writes'][1:] == ['/cart/up/304858', '/cart/down/304858', '/cart/down/304858']
    assert client.request('DELETE', API, json=row).json()['removed_quantity'] == 1
    assert client.request('DELETE', API, json=row).status_code == 404
    assert client.put(API + '/quantity', json={**row, 'quantity': 0}).json()['after_quantity'] == 0
    shop['quantity'] = 2
    assert client.put(API + '/quantity', json={**row, 'quantity': 0}).json()['cart']['items'] == []
    shop['quantity'] = 3
    assert client.request('DELETE', API + '/clear', json=creds).json()['removed_quantity'] == 3
    assert client.request('DELETE', API + '/clear', json=creds).json()['removed_quantity'] == 0
    assert shop['writes'].count('/cart/clear') == 1


@pytest.mark.parametrize(('message', 'kind', 'reason'), [
    ('「迷子のバンド 椎名立希(SR)」は販売制限しております。一度に販売制限数を超える購入はできません。', 'stock_exceeded', 'purchase_limit'),
    ('「カード」の在庫が不足しております。一度に在庫数を超える購入はできません。', 'stock_exceeded', 'stock'),
    ('「カード」の在庫が不足しております。該当商品をカートから削除しました。', 'stock_exceeded', 'stock'),
    ('商品を購入できません。', 'cart_rejected', None),
])
def test_add_rejection_uses_store_message(client, shop, message, kind, reason):
    shop.update(quantity=4, add_reply={'done': False, 'messages': [message]})
    result = client.post(API, json={**BODY, 'product_url': URL, 'counter': 2})
    assert result.status_code == 409
    assert result.json()['detail']['type'] == 'hobbystation_' + kind
    assert result.json()['detail'].get('reason') == reason
    assert shop['writes'] == ['/products/add_cart/305273']


def test_secrets_origin_and_upstream_scope(client, shop):
    assert client.post(API, json={**BODY, 'product_url': 'https://example.org/lo/product/detail/1'}).status_code == 422
    bad = client.post(API + '/query', json={'credentials': {'eccube': 'test-secret; other=value'}})
    assert bad.status_code == 422 and 'test-secret' not in bad.text
    assert client.post(API + '/query', json=BODY, headers={'Origin': 'https://other.test'}).status_code == 403
    assert not shop['calls']
    challenge = client.post(API, json={'credentials': {'eccube': 'test-session'}, 'product_url': URL})
    assert challenge.json()['detail']['type'] == 'hobbystation_product_access_check'
    shop['headers'] = {'Set-Cookie': 'eccube=replacement; Path=/'}
    assert client.request('DELETE', API + '/clear', json=BODY).json()['detail']['type'] == 'hobbystation_credentials_rejected'
    assert not shop['writes']
    client.headers.pop('Authorization')
    assert client.post(API + '/query', json=BODY).status_code == 401


def test_unreadable_rows_and_write_verification(client, shop):
    shop['html'] = cart_html(2).replace('1,200円', '???')
    query = client.post(API + '/query', json=BODY).json()
    assert query['complete'] is False and query['subtotal'] is None and query['warnings']
    assert client.request('DELETE', API + '/clear', json=BODY).status_code == 502
    assert not shop['writes']
    del shop['html']
    shop.update(quantity=1, ignore_write=True)
    result = client.put(API + '/quantity', json={**BODY, **SKU, 'quantity': 3})
    assert result.status_code == 409 and result.json()['detail']['after_quantity'] == 1
    assert shop['writes'] == ['/cart/up/304858']
    shop['redirect'] = 'https://other.test/cart'
    assert client.request('DELETE', API, json={**BODY, **SKU}).status_code == 502


def test_partial_quantity_adjustment_stops_after_unverified_write(client, shop):
    shop.update(quantity=1, fail_read_at=3)
    result = client.put(API + '/quantity', json={**BODY, **SKU, 'quantity': 4})
    assert result.status_code == 502
    assert result.json()['detail'] == {'type': 'hobbystation_invalid_response', 'outcome': 'unknown',
                                      'before_quantity': 1, 'last_verified_quantity': 2, 'requested_quantity': 4}
    assert shop['writes'] == ['/cart/up/304858', '/cart/up/304858']
