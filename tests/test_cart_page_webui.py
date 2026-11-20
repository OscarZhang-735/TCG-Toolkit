"""Three cart routes use the same controls with their own product identities."""
import json
import os
import re
from collections import Counter

import httpx
import pytest

from test_yuyutei_cart import client, upstream


@pytest.mark.skipif(os.getenv('TCG_BROWSER_TEST') != '1', reason='Set TCG_BROWSER_TEST=1 for Chromium acceptance.')
def test_stock_and_quantity_controls_on_all_cart_routes(client):
    from playwright.sync_api import expect, sync_playwright

    paths = {'yuyutei': '#cart', 'hobbystation': '#cart-hobbystation', 'cardmax': '#cart-cardmax'}
    credentials = {'yuyutei': {'cookie': 'test-cookie', 'csrf_token': 'test-token'},
                   'hobbystation': {'eccube': 'test-cart', 'eccube_bot_check': 'test-token'},
                   'cardmax': {'tempid': 'test-cart', 'login_id': 'test-login', 'member_key': '123_key=test-token'}}
    rows = {}
    for shop in paths:
        rows[shop] = []
        for num in ('401', '402'):
            if shop == 'yuyutei':
                url = f'https://yuyu-tei.jp/sell/ws/card/test/{num}'
                identity = {'gid': 8, 'ver': 'test', 'cid': num, 'kizu': 0, 'type': 'normal', 'game': 'ws'}
            elif shop == 'hobbystation':
                url = f'https://www.hobbystation-single.jp/ws/product/detail/{num}'
                identity = {'product_id': num, 'product_class_id': '9' + num}
            else:
                url = f'https://www.cardmax.jp/shopdetail/{num}'
                identity = {'brandcode': num}
            rows[shop].append({**identity, 'name': f'{shop} {num}', 'product_url': url,
                               'unit_price': 50, 'image_url': None, 'quantity': 1, 'removable': True})
    reads, queries, adds = Counter(), Counter(), []
    unavailable = set()

    def cart(shop):
        count = sum(row['quantity'] for row in rows[shop])
        return {'items': rows[shop], 'total_quantity': count, 'subtotal': count * 50,
                'currency': 'JPY', 'complete': True, 'warnings': []}

    def serve(route):
        req = route.request
        url = httpx.URL(req.url)
        if url.host != 'testserver':
            return route.abort()
        shop = url.path.split('/')[2] if url.path.startswith('/api/') else None
        if shop in paths:
            if url.path.endswith('/cart/query'):
                queries[shop] += 1
                if shop in unavailable:
                    return route.fulfill(status=403, json={'detail': {'type': shop + '_credentials_rejected'}})
                return route.fulfill(json=cart(shop))
            if url.path.endswith('/product/stock') or url.params.get('stock') == 'true':
                product = url.params['cid'] if shop == 'yuyutei' else url.params['product_url']
                reads[shop, product] += 1
                stock = {'stock_status': 'in_stock', 'stock_quantity': 5, 'game': 'ws', 'rarity': 'RR',
                         'card_number': 'TEST-' + product.rsplit('/', 1)[-1]}
                if shop == 'hobbystation' and product == rows[shop][1]['product_url']:
                    stock.update(stock_quantity=None, stock_text='在庫数：◯')
                return route.fulfill(json=stock)
            if url.path.endswith('/cart') and req.method == 'POST':
                body = req.post_data_json
                adds.append((shop, body.get('product_url')))
                rows[shop][0]['quantity'] += body['counter']
                return route.fulfill(json={'after_quantity': rows[shop][0]['quantity'], 'cart': cart(shop)})
            if url.path.endswith('/product'):
                return route.fulfill(json={'card_number': 'TEST-' + url.params['cid'], 'rarity': 'R'})
        if url.path == '/api/currency/convert':
            return route.fulfill(json={'from': 'JPY', 'to': url.params['to'], 'amount': 1, 'result': 1})
        result = client.request(req.method, req.url, content=req.post_data_buffer,
                                headers={k: v for k, v in req.headers.items() if k in ('origin', 'content-type')})
        return route.fulfill(status=result.status_code, body=result.content,
                             content_type=result.headers.get('content-type', 'application/octet-stream'))

    user_id = client.get('/api/auth/me').json()['id']
    saved = {f'tcg.{shop}.credentials': {'userId': user_id, 'credentials': value}
             for shop, value in credentials.items()}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.getenv('TCG_BROWSER_CHANNEL') or None)
        page = browser.new_page(locale='en-US')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', serve)
        page.add_init_script("localStorage.setItem('tcg.session'," + json.dumps(client.headers['Authorization'][7:])
                             + "); for (const [key,value] of Object.entries(" + json.dumps(saved)
                             + ")) sessionStorage.setItem(key,JSON.stringify(value));")
        for shop, fragment in paths.items():
            page.goto(f'https://testserver/?shop={shop}{fragment}')
            expect(page.locator('#cart-kinds')).to_have_text('2')
            page.locator('[data-view=list]:visible').click()
            expect(page.locator('#cart-table')).to_be_visible()
            label = {'yuyutei': 'YuYu-Tei', 'hobbystation': 'Hobby Station', 'cardmax': 'Card Max'}[shop]
            buttons = page.locator('#cart-rows .cart-stepper').first.get_by_role('button')
            for button, hint in [(buttons.first, f'Remove one from your {label} cart'),
                                 (buttons.last, f'Add one to your {label} cart')]:
                expect(button).to_have_attribute('title', hint)
                expect(button).to_have_accessible_name(hint)
            product_link = page.locator('#cart-rows .product-summary a').first
            product_link.click()
            expect(page.locator('#detail-description')).to_have_text(f'This link will open {label} in a new tab.')
            expect(page.locator('#detail-confirm')).to_have_attribute('href', rows[shop][0]['product_url'])
            page.locator('#detail-cancel').click()
            single = page.locator('.cart-stock-fetch:visible')
            expect(single).to_have_count(2)
            expect(page.locator('.product-summary:visible, .card-name:visible').first).to_contain_text(shop)
            expect(page.locator('#cart-rows')).to_contain_text('TEST-402')
            if shop != 'yuyutei':
                expect(page.locator('#cart-rows')).to_contain_text('RR')
            expect(page.locator('.cart-stock-control .in_stock:visible')).to_have_count(0)
            single.first.click()
            badges = page.locator('.cart-stock-control .in_stock:visible')
            expect(badges).to_have_count(1)
            if shop == 'hobbystation':
                badges.first.hover()
                expect(badges.first).to_have_text('In stock')
                expect(badges.first).to_have_attribute('title', re.compile(r'\b5\b'))
            page.locator('[data-view=grid]:visible').click()
            expect(page.locator('#cart-grid')).to_be_visible()
            expect(badges).to_have_count(1)
            page.locator('#cart-get-stock').click()
            expect(badges).to_have_count(2)
            if shop == 'hobbystation':
                expect(badges).to_have_text(['In stock', 'In stock'])
                expect(badges.first).to_have_attribute('title', re.compile(r'\b5\b'))
                badges.nth(1).hover()
                expect(badges.nth(1)).to_have_attribute('title', re.compile('在庫数：◯'))
            for index, row in enumerate(rows[shop]):
                key = row['cid'] if shop == 'yuyutei' else row['product_url']
                assert reads[shop, key] == (2 if index == 0 else 1) + int(shop != 'yuyutei')
            if shop != 'yuyutei':
                control = page.locator('.cart-stepper:visible').first
                control.locator('button').last.click()
                expect(control.locator('input')).to_have_value('2')
                assert adds[-1] == (shop, rows[shop][0]['product_url'])
            page.evaluate("location.hash = '#search'")
            for merchant in paths:
                selector = f'input[name=merchant][value={merchant}]'
                if not page.locator(selector).is_checked():
                    page.locator(f'label:has({selector})').click()
            summaries = page.locator('#cart-summary .cart-summary:visible')
            expect(summaries).to_have_count(3)
            summary = page.locator(f'#cart-summary [data-source="{shop}"]')
            expect(summary).to_contain_text('Cart ' + str(cart(shop)['total_quantity']))
            expect(summary.locator('a')).to_have_attribute('href', fragment)
            before = queries.copy()
            rows[shop][0]['quantity'] += 1
            summary.locator('button').click()
            expect(summary).to_contain_text('Cart ' + str(cart(shop)['total_quantity']))
            assert queries - before == Counter({shop: 1})
            page.locator(f'label:has(input[name=merchant][value={shop}])').click()
            expect(summaries).to_have_count(2)
        unavailable.add('cardmax')
        page.goto('https://testserver/?retry#cart-cardmax')
        expect(page.locator('#cart-empty h3')).to_contain_text('cookies')
        expect(page.locator('#cart-stats')).to_be_hidden()
        unavailable.clear()
        page.locator('#cart-page-refresh').click()
        expect(page.locator('#cart-kinds')).to_have_text('2')
        assert not errors
        browser.close()
