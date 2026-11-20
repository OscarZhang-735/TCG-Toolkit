import json
import os
from urllib.parse import parse_qsl

import httpx
import pytest

from tcg_toolkit.cart.cardmax import BASKET, BASE
from test_yuyutei_cart import client, upstream


API = '/api/cardmax/cart'
CODE = '000000235105'
URL = BASE + '/shopdetail/' + CODE
BODY = {'credentials': {'tempid': 'test-cart', 'login_id': 'test-member', 'member_key': '123_key=test-signature'}}
ROW = {**BODY, 'brandcode': CODE}
ADD = {**BODY, 'product_url': URL, 'counter': 1}


def html(text):
    return httpx.Response(200, content=text.encode('euc_jis_2004'), headers={
        'Content-Type': 'text/html; charset=EUC-JP', 'Set-Cookie': 'slvd=analytics; Path=/'})


def row(code, quantity):
    return f'''<form id="forms1" action="basket.html" method="post">
        <input name="brandcode" value="{code}"><input name="type" value="">
        <input name="orgamount" value="{quantity}"><input name="orgopts" value="通常">
        <tr><td class="basket-name"><a href="/shopdetail/{code}/">テスト</a></td>
        <td><input name="amount" value="{quantity}"></td><td class="basket-price">{30 * quantity}円</td></tr></form>'''


@pytest.fixture
def shop(client, monkeypatch):
    state = {'items': {}, 'writes': []}

    def handle(self, req):
        assert req.headers['cookie'] == 'tempid=test-cart; login_id=test-member; 123_key=test-signature'
        assert 'authorization' not in req.headers
        if str(req.url) == URL:
            assert req.method == 'GET'
            return html(f'''<span class="M_item-stock-small">残りあと4個</span>
                <form name="form1" action="/shop/basket.html" method="post"><input name="admin_id" value="cardmax">
                <input name="brandcode" value="{CODE}"><input name="brandcode" value="{CODE}">
                <select name="amount"><option value="1">1</option></select></form>''')
        assert str(req.url) == BASKET
        if req.method == 'POST':
            assert req.headers['Content-Type'] == 'application/x-www-form-urlencoded'
            pairs = parse_qsl(req.content.decode('ascii'), keep_blank_values=True, encoding='euc_jis_2004')
            data = dict(pairs)
            state['writes'].append(data)
            code = data['brandcode']
            if 'type' not in data:
                assert pairs.count(('brandcode', CODE)) == 2
                if code in state['items']:
                    return html("<script>alert('既に該当商品は買い物かごに入っております。')</script>")
                state['items'][code] = int(data['amount'])
            elif data['type'] == 'del' and code == 'all':
                state['items'].clear()
            else:
                assert data['orgopts'] == '通常' and int(data['orgamount']) == state['items'][code]
                if data['type'] == 'upd':
                    if not state.get('ignore_write'):
                        state['items'][code] = int(data['amount'])
                else:
                    assert data['type'] == 'del'
                    del state['items'][code]
        contents = ''.join(row(k, v) for k, v in state['items'].items()) or '買い物かごに商品がありません'
        logout = '' if state.get('guest') else '<a href="/shop/logout.html">ログアウト</a>'
        return html(f'''{logout}<form name="sslform"><input name="ssl_tempid" value="test-cart"></form>
            <div id="basket"><table class="basket">{contents}</table></div>
            <form name="delform" method="post" action="basket.html"><input name="brandcode">
            <input name="type" value="del"></form>''')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', handle)
    return state


def test_member_cart_writes_preserve_other_items_and_verify_totals(client, shop):
    shop['items']['000000174654'] = 1
    baseline = client.post(API + '/query', json=BODY).json()
    key = {'Idempotency-Key': 'cardmax-test'}
    add = client.post(API, json=ADD, headers=key)
    assert add.status_code == 200 and add.json()['after_quantity'] == 1
    assert client.post(API, json=ADD, headers=key).headers['idempotency-replayed'] == 'true'
    change = client.put(API + '/quantity', json={**ROW, 'quantity': 2}).json()['cart']
    assert change['subtotal'] == 90 and change['total_quantity'] == 3
    item = next(i for i in change['items'] if i['brandcode'] == CODE)
    assert item['name'] == 'テスト' and item['unit_price'] == 30
    more = client.post(API, json={**ADD, 'counter': 2})
    assert more.json()['after_quantity'] == 4 and more.json()['observed_delta'] == 2
    assert shop['writes'][-1]['type'] == 'upd'
    assert client.request('DELETE', API, json=ROW).json()['cart'] == baseline
    assert len(shop['writes']) == 4
    assert client.request('DELETE', API + '/clear', json=BODY).json()['cart']['total_quantity'] == 0


def test_rejects_guest_invalid_cookies_and_foreign_urls_before_writing(client, shop):
    shop['guest'] = True
    denied = client.post(API, json=ADD)
    assert denied.status_code == 409 and denied.json()['detail']['type'] == 'cardmax_credentials_rejected'
    for invalid in ({**ADD, 'product_url': 'https://outside.example/shopdetail/1'},
                    {**ADD, 'credentials': {**BODY['credentials'], 'member_key': '123_key=private; extra=x'}}):
        result = client.post(API, json=invalid)
        assert result.status_code == 422 and result.json()['detail'] == 'invalid_cardmax_request'
        assert 'private' not in result.text
    assert denied.headers['cache-control'] == 'no-store' and not shop['writes']


def test_stock_and_ignored_writes(client, shop):
    shop['items'][CODE] = 1
    result = client.post(API, json={**ADD, 'counter': 4})
    assert result.json()['detail'] == {'type': 'cardmax_stock_exceeded', 'outcome': 'not_sent'}
    assert not shop['writes']
    shop['ignore_write'] = True
    result = client.put(API + '/quantity', json={**ROW, 'quantity': 2})
    assert result.status_code == 409
    assert result.json()['detail']['after_quantity'] == 1


@pytest.mark.skipif(os.getenv('TCG_BROWSER_TEST') != '1', reason='Set TCG_BROWSER_TEST=1 for Chromium acceptance.')
def test_search_and_favorites_share_verified_member_cart(client, shop):
    from playwright.sync_api import sync_playwright, expect

    item = {'source': 'cardmax', 'game': 'sev', 'name': 'Test card', 'rarity': None, 'card_number': 'BP22-T03',
            'price': 30, 'currency': 'JPY', 'stock_status': 'in_stock', 'stock_quantity': 4, 'stock_text': '4',
            'product_url': URL, 'image_url': None, 'cart': None}
    assert client.post('/api/favorites', json=item).status_code in (200, 201)

    def serve(route):
        req = route.request
        url = httpx.URL(req.url)
        if url.host != 'testserver':
            return route.abort()
        if url.path == '/api/search':
            return route.fulfill(json={'items': [item], 'page': 1, 'next_page': None, 'source': 'cardmax'})
        if url.path == '/api/currency/convert':
            return route.fulfill(json={'from': 'JPY', 'to': url.params['to'], 'amount': 1, 'result': 0.05})
        result = client.request(req.method, req.url, content=req.post_data_buffer,
                                headers={k: v for k, v in req.headers.items() if k in ('origin', 'content-type', 'idempotency-key')})
        route.fulfill(status=result.status_code, body=result.content,
                      content_type=result.headers.get('content-type', 'application/octet-stream'))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.getenv('TCG_BROWSER_CHANNEL') or None)
        page = browser.new_page(locale='zh-CN')
        page.route('**/*', serve)
        page.add_init_script("localStorage.setItem('tcg.session', " + json.dumps(client.headers['Authorization'][7:]) + ")")
        page.goto('https://testserver/#account')
        expect(page.locator('#cm-cart-state-label')).not_to_have_text('')
        chips = page.locator('.cart-settings-meta .cart-state')
        expect(chips).to_have_count(3)
        for width in (390, 1280):
            page.set_viewport_size({'width': width, 'height': 800})
            sizes = chips.evaluate_all('(chips) => chips.map(chip => [chip.offsetWidth, chip.offsetHeight])')
            assert sizes[0] == sizes[1] == sizes[2]
            expect(page.locator('#cm-cart-state-icon')).to_have_css('display', 'none')
        for field, value in [('tempid', 'test-cart'), ('login-id', 'test-member'),
                             ('member-name', '123'), ('member-value', 'test-signature')]:
            page.locator('#cm-cart-' + field).fill(value)
        page.locator('#cm-cart-save').click()
        expect(page.locator('#cm-cart-status.error')).to_be_visible()
        page.locator('#cm-cart-member-name').fill('123_key')
        page.locator('#cm-cart-save').click()
        expect(page.locator('#cm-cart-body')).to_be_hidden()
        expect(page.locator('#cm-cart-state-icon')).not_to_have_css('display', 'none')
        page.evaluate("location.hash = '#search'")
        page.locator('label.chip:has(input[name=merchant][value=cardmax])').click()
        for checkbox in page.locator('input[name=merchant], input[name=source]').all():
            if checkbox.is_checked() and checkbox.get_attribute('value') not in ('sev', 'cardmax'):
                checkbox.locator('..').click()
        game = page.locator('input[name=source][value=sev]')
        if not game.is_checked():
            game.locator('..').click()
        page.locator('#keyword').fill('BP22-T03')
        page.locator('#search-button').click()
        control = page.locator('#search .cart-stepper:visible').first
        expect(control.locator('button').last).to_have_attribute('title', '向 Card Max 购物车添加一张')
        control.locator('button').last.click()
        expect(control.locator('input')).to_have_value('1')
        page.evaluate("location.hash = '#favorites'")
        favorite = page.locator('#favorites-panel .cart-stepper:visible').first
        expect(favorite.locator('button').first).to_have_accessible_name('从 Card Max 购物车减少一张')
        expect(favorite.locator('input')).to_have_value('1')
        favorite.locator('input').fill('2')
        favorite.locator('input').press('Enter')
        expect(favorite.locator('button').first).to_be_enabled()
        expect(favorite.locator('input')).to_have_value('2')
        favorite.locator('input').fill('0')
        favorite.locator('input').press('Enter')
        expect(favorite.locator('input')).to_have_value('0')
        expect(favorite.locator('button').last).to_be_enabled()
        expect(favorite.locator('button').first).to_be_disabled()
        assert shop['items'] == {}
        browser.close()
