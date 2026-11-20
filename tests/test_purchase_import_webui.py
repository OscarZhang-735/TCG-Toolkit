"""Paste a mixed order, review it and add only the confirmed quantities."""
import json
import os

import httpx
import pytest

from test_cards import client


@pytest.mark.skipif(os.getenv('TCG_BROWSER_TEST') != '1', reason='Set TCG_BROWSER_TEST=1 for Chromium acceptance.')
def test_import_pasted_purchase_history(client):
    from playwright.sync_api import expect, sync_playwright

    wsr = dict(game='WSR', card_number='OS01/R01-047S', rarity='SR', name='興味本位の身体検査', image_url=None)
    # Deliberately use a number with the same prefix as WSR: the official record belongs to WS.
    ws = dict(game='WS', card_number='OS01/R01-048S', rarity='SR', name='別のゲームのカード', image_url=None)
    shared = {**wsr, 'card_number': 'OS01/R01-099S'}
    incomplete = {**wsr, 'card_number': 'OS01/R01-098S'}
    official_cards = [wsr, ws, shared, {**shared, 'game': 'WS'}, incomplete]
    saved = client.post('/api/cards', json={**wsr, 'name': 'My title', 'quantity': 2, 'price': 42})
    assert saved.status_code == 201
    records = [
        'OS01/R01-047S SR 興味本位の身体検査 180円 数量:6 小計1,080円',
        'OS01/R01-048S SR 別のゲームのカード 5,980円 数量:1 小計5,980円',
        'OS01/R01-050S SR 紅に染まる時間 180円 数量:3 小計540円',
        'OS01/R01-099S SR 興味本位の身体検査 180円 数量:1 小計180円',
        'OS01/R01-098S SR 興味本位の身体検査 180円 数量:1 小計180円',
    ]
    queries, keys = [], []

    def serve(route):
        request = route.request
        url = httpx.URL(request.url)
        if url.host != 'testserver':
            return route.abort()
        if url.path.startswith('/api/official/'):
            game = url.path.split('/')[3]
            word = url.params['word']
            queries.append((game, word))
            if word == incomplete['card_number'] and game == 'ws':
                return route.fulfill(status=503, json={'detail': 'official_unavailable'})
            data = [card for card in official_cards if card['game'].lower() == game and word == card['card_number']]
            return route.fulfill(json={'items': data, 'total': len(data)})
        headers = {k: v for k, v in request.headers.items() if k in ('content-type', 'origin', 'idempotency-key')}
        result = client.request(request.method, request.url, headers=headers, content=request.post_data_buffer)
        if url.path == '/api/cards/purchases':
            # The first write succeeds but the response is lost. Retrying must keep the original request key.
            keys.append(headers['idempotency-key'])
            if len(keys) == 1:
                assert result.status_code == 200, result.text
                return route.abort()
        return route.fulfill(status=result.status_code, body=result.content,
                             content_type=result.headers.get('content-type', 'application/octet-stream'))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.getenv('TCG_BROWSER_CHANNEL') or None)
        page = browser.new_page(locale='en-US')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', serve)
        page.add_init_script("localStorage.setItem('tcg.session'," + json.dumps(client.headers['Authorization'][7:]) + ');')
        page.goto('https://testserver/#cards')
        page.locator('#cards-purchase-import').click()
        expect(page.locator('#purchase-source')).to_have_value('yuyutei')
        page.locator('#purchase-text').fill('\n'.join(records))
        submit = page.locator('#purchase-submit')
        games = page.locator('#purchase-games')
        expect(submit).to_be_disabled()
        games.get_by_text('WSR', exact=True).click()
        submit.click()
        dialog = page.locator('#purchase-dialog[open]')
        rows = dialog.locator('#purchase-rows li')
        expect(rows).to_have_count(5)
        expect(rows.nth(1)).to_contain_text('No matching card')
        expect(rows.nth(3).get_by_role('spinbutton')).to_be_visible()
        assert {game for game, _ in queries} == {'wsr'}
        dialog.locator('#purchase-cancel').click()
        expect(dialog).to_have_count(0)
        assert not keys
        page.locator('#cards-purchase-import').click()
        expect(rows).to_have_count(5)
        games.get_by_text('WS', exact=True).click()
        expect(rows).to_have_count(0)
        expect(submit).to_have_text('Look up cards')
        queries.clear()
        submit.click()
        expect(rows).to_have_count(5)
        expect(rows.nth(0)).to_contain_text('WSR · OS01/R01-047S')
        expect(rows.nth(1)).to_contain_text('WS · OS01/R01-048S')
        expect(rows.nth(2)).to_contain_text('No matching card')
        expect(rows.nth(3)).to_contain_text('Multiple matching cards')
        expect(rows.nth(4)).to_contain_text('Lookup failed')
        expect(rows.nth(3).locator('select, input')).to_have_count(0)
        expect(rows.nth(4).get_by_role('spinbutton')).to_have_count(0)
        assert sorted(queries) == sorted((game, record.split()[0]) for game in ('wsr', 'ws') for record in records)
        assert not keys
        assert [row['quantity'] for row in client.get('/api/cards').json()['items']] == [2]
        quantity = rows.nth(0).get_by_role('spinbutton')
        expect(quantity).to_have_value('6')
        quantity.fill('4')
        expect(page.locator('#purchase-status')).to_have_text('Order lines to import: 2 · Copies to add: 5 · Lines skipped: 3')
        page.get_by_role('button', name='Confirm import').click()
        retry = page.get_by_role('button', name='Retry import')
        expect(retry).to_be_visible()
        expect(quantity).to_be_disabled()
        retry.click()
        expect(page.locator('#purchase-status')).to_have_text('Added 5 copies to your card library.')
        assert len(keys) == 2 and keys[0] == keys[1]
        page.locator('#purchase-cancel').click()
        expect(page.locator('#cards-rows tr')).to_have_count(2)
        by_game = {row['game']: row for row in client.get('/api/cards').json()['items']}
        assert (by_game['WSR']['quantity'], by_game['WSR']['name'], by_game['WSR']['price']) == (6, 'My title', 42)
        assert (by_game['WS']['quantity'], by_game['WS']['price']) == (1, None)
        assert not errors
        browser.close()
