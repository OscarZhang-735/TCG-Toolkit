import json
import os

import httpx
import pytest

from tcg_toolkit.catalog import prices
from test_cards import CARD, client


@pytest.mark.skipif(os.getenv('TCG_BROWSER_TEST') != '1', reason='Set TCG_BROWSER_TEST=1 for Chromium acceptance.')
def test_single_and_bulk_price_source(client, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    assert client.post('/api/cards', json=CARD).status_code == 201
    calls = []

    def offers(source, game, number):
        calls.append(source.value)
        return [{**CARD, 'card_number': number, 'price': 80 if source == 'hobbystation' else 220,
                 'currency': 'JPY', 'stock_quantity': 5, 'stock_status': 'in_stock',
                 'product_url': 'https://www.cardmax.jp/shopdetail/1' if source == 'cardmax'
                 else 'https://www.hobbystation-single.jp/ws/product/detail/1'}]

    monkeypatch.setattr(prices, 'merchant_prices', offers)

    def serve(route):
        req = route.request
        url = httpx.URL(req.url)
        if url.host != 'testserver':
            return route.abort()
        if url.path.startswith('/api/official/'):
            return route.fulfill(json={'items': [], 'total': 0})
        result = client.request(req.method, req.url, content=req.post_data_buffer,
                                headers={k: v for k, v in req.headers.items() if k in ('origin', 'content-type')})
        route.fulfill(status=result.status_code, body=result.content, content_type=result.headers.get('content-type'))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.getenv('TCG_BROWSER_CHANNEL') or None)
        page = browser.new_page()
        page.route('**/*', serve)
        page.add_init_script("localStorage.setItem('tcg.session'," + json.dumps(client.headers['Authorization'][7:]) + ")")
        page.goto('https://testserver/#cards')
        page.locator('#cards-table button:has(.bi-pencil)').click()
        source = page.locator('#card-price-source')
        expect(source).to_have_value('yuyutei')
        source.select_option('hobbystation')
        for width in (320, 390, 800, 1280):
            page.set_viewport_size({'width': width, 'height': 800})
            price, currency, merchant, lookup = [
                page.locator('#' + control).bounding_box()
                for control in ('card-price', 'card-currency', 'card-price-source', 'card-price-lookup')
            ]
            assert all(abs(box['y'] - price['y']) < 1 for box in (currency, merchant, lookup))
            assert price['width'] >= 40 and lookup['x'] + lookup['width'] <= width, (width, price, currency, merchant, lookup)
        page.locator('#card-price-lookup').click()
        page.locator('.price-offer-pick').click()
        expect(page.locator('#card-price')).to_have_value('80')
        source.select_option('cardmax')
        expect(page.locator('#price-lookup-results')).to_be_hidden()
        page.locator('#card-price-lookup').click()
        page.locator('.price-offer-pick').click()
        expect(page.locator('#card-price')).to_have_value('220')
        page.locator('#cards-cancel').click()
        page.locator('#cards-select').click()
        page.locator('#cards-select-all').check()
        page.locator('#cards-bulk-price').click()
        radios = page.locator('#cards-price-sources')
        expect(radios.locator('input:checked')).to_have_value('yuyutei')
        radios.locator('label:has(input[value=hobbystation])').click()
        page.locator('#cards-price-start').click()
        expect(page.locator('#cards-price-finish')).to_be_visible()
        assert client.get('/api/cards').json()['items'][0]['price'] == 80
        assert calls == ['hobbystation', 'cardmax', 'hobbystation']
        browser.close()
