"""Opt-in Chromium acceptance: real WebUI/API/PostgreSQL, fixture providers."""
from dataclasses import replace
from base64 import b64encode
from pathlib import Path
import re
import os
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import HTTPException

from auth_helpers import install_mailbox, register_user
from card_photos import corner_error, photo
from tcg_toolkit.grading import adapters, lookup as grading
from tcg_toolkit.grading.concurrency import cancellable_client
from tcg_toolkit.web import app

pytestmark = pytest.mark.skipif(os.getenv('TCG_BROWSER_TEST') != '1', reason='Set TCG_BROWSER_TEST=1 for Chromium acceptance.')
FIXTURES = Path(__file__).parent / 'fixtures'


def test_graded_webui(monkeypatch, tmp_path):
    from playwright.sync_api import sync_playwright, expect

    # Uploaded card photos go to an in-memory bucket; the page serves its URLs from a fixture.
    monkeypatch.setenv('TCG_AVATAR_COS_URL', 'https://cos.test')
    bucket, removed = {}, []

    class Stored:
        status_code = 200

        def raise_for_status(self):
            pass

    monkeypatch.setattr('tcg_toolkit.media.objects.httpx.put', lambda url, content, **kwargs: (bucket.__setitem__(url, content), Stored())[1])
    monkeypatch.setattr('tcg_toolkit.media.objects.httpx.delete', lambda url, **kwargs: (removed.append(url), bucket.pop(url, None), Stored())[2])
    slab, slab_corners = photo('slab', 'busy', 9, .06, (1600, 1200), 21)
    (tmp_path / 'slab.jpg').write_bytes(slab)
    flat, _ = photo('card', 'dark', -4, .03, (1200, 1600), 22)
    (tmp_path / 'card.jpg').write_bytes(flat)
    state = {'fail': False}
    calls, titles = [], {}
    blocking, released = '11111111', threading.Event()
    for provider, number in [('psa', '98428602'), ('cgc', '6091052028'), ('ars', '047088950114'), ('pcgcn', '10198854')]:
        source = (FIXTURES / f'{provider}_certificate.{"json" if provider == "pcgcn" else "html"}').read_text(encoding='utf-8')
        parsed = getattr(adapters, f'parse_{provider}')(json.loads(source) if provider == 'pcgcn' else source, number)
        titles[provider] = grading.present(provider, parsed['certificate'])[0]

        def fetch(requested, result=parsed):
            calls.append((result['provider'], requested))
            if requested == blocking:
                # Like a slow upstream page: only the cancel (closing the client) ends it.
                class Upstream:
                    event_hooks = {'request': []}
                    def close(self):
                        released.set()
                cancellable_client(Upstream())
                assert released.wait(timeout=30)
                raise HTTPException(502, detail=f"{result['provider']}_unavailable")
            if state['fail']:
                raise HTTPException(504, detail=f"{result['provider']}_timeout")
            time.sleep(0.2)  # expose the real pending UI before returning
            return {**result, 'cert_number': requested}

        monkeypatch.setitem(grading.PROVIDERS, provider, replace(grading.PROVIDERS[provider], fetch=fetch))

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
        monkeypatch.setenv('TCG_TRUSTED_ORIGINS', origin)
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, timeout_graceful_shutdown=5))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started
            install_mailbox()
            with httpx.Client(base_url=origin, headers={'Origin': origin}, trust_env=False) as client:
                registered = register_user(client, 'browser_acceptance')
                assert registered.status_code == 201
                token = registered.json()['token']
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 1050}, locale='en-US')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.add_init_script("""localStorage.setItem('language', 'en');
                    Object.defineProperty(window, 'BarcodeDetector', {value: undefined, configurable: true});""")
                # Keep image loading deterministic while retaining real image preview behavior.
                image = (Path(__file__).parents[1] / 'src/tcg_toolkit/static/game-wsr.webp').read_bytes()
                page.route('https://**/*', lambda route: route.fulfill(status=200, body=image, content_type='image/webp')
                           if route.request.resource_type == 'image' else route.continue_())

                def exchange_rate(route):
                    currency = httpx.URL(route.request.url).params['to']
                    route.fulfill(json={'from': 'JPY', 'to': currency, 'amount': 1,
                                        'result': {'CNY': 0.05, 'CAD': 0.01}[currency]})

                page.route('**/api/currency/convert?*', exchange_rate)
                page.goto(origin)
                page.evaluate('(token) => localStorage.setItem("tcg.session", token)', token)
                page.goto(origin + '/#graded')
                page.reload()  # a fragment-only navigation does not restore the newly set session
                expect(page.locator('#graded-panel')).to_be_visible()
                expect(page.locator('#graded-add')).to_be_enabled()
                expect(page.locator('#graded-empty')).to_be_visible()
                expect(page.locator('#graded-stats')).to_be_visible()
                expect(page.locator('#graded-stats .stat-value')).to_have_text(['0', 'JPY -'])
                # Filters stay on screen with no cards, here and on the other card pages.
                expect(page.locator('#graded-filters')).to_be_visible()
                expect(page.locator('#graded-provider-chips label')).to_have_count(len(grading.PROVIDERS))
                # Logo chips like the card library's game filter; every logo file must load.
                logos = page.locator('#graded-provider-chips img.chip-logo')
                expect(logos).to_have_count(len(grading.PROVIDERS))
                page.wait_for_function("[...document.querySelectorAll('#graded-provider-chips img.chip-logo')].every(img => img.complete && img.naturalWidth > 0)")
                page.goto(origin + '/#cards')
                expect(page.locator('#cards-empty')).to_be_visible()
                expect(page.locator('#cards-filters')).to_be_visible()
                page.goto(origin + '/#favorites')
                expect(page.locator('#favorites-empty')).to_be_visible()
                expect(page.locator('#favorites-panel .filters')).to_be_visible()
                page.goto(origin + '/#graded')
                expect(page.locator('#graded-filters')).to_be_visible()
                expect(page.locator('a[href="#graded"]')).to_have_attribute('aria-current', 'page')

                def add(value, provider=None, price=None, currency='JPY'):
                    page.locator('#graded-add').click()
                    if provider:
                        page.locator('#graded-provider').select_option(provider)
                    page.locator('#graded-cert').fill(value)
                    if price is not None:
                        page.locator('#graded-add-price').fill(price)
                        page.locator('#graded-add-currency').select_option(currency)
                    page.locator('#graded-submit').click()

                # Official links pick the provider; the saved row survives a reload.
                add('https://www.cgccards.com/certlookup/6091052028/10_0/', price='12.34', currency='CNY')
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(1)
                expect(page.locator('#graded-rows')).to_contain_text('PRISTINE 10')
                page.reload()
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(1)
                expect(page.locator('#graded-price-total')).to_have_text('JPY 247')
                # Unit price per row: display currency first, entered amount underneath.
                expect(page.locator('#graded-rows tr[data-id] td.numeric .converted-price')).to_have_text('JPY 247CNY 12.34')
                page.locator('#graded-rows .graded-title').click()
                # Price and display name are edited under the images, in aligned rows.
                expect(page.locator('.graded-detail-side #graded-price-form')).to_be_visible()
                price_box = page.locator('#graded-detail-price').bounding_box()
                name_box = page.locator('#graded-detail-name').bounding_box()
                assert abs(price_box['x'] - name_box['x']) < 3 and name_box['y'] > price_box['y']
                assert price_box['y'] > page.locator('.graded-detail-images').bounding_box()['y']
                expect(page.locator('#graded-detail-price')).to_have_value('12.34')
                expect(page.locator('#graded-detail-currency')).to_have_value('CNY')
                page.locator('#graded-detail-price').fill('25.5')
                page.locator('#graded-detail-currency').select_option('CAD')
                page.locator('#graded-price-save').click()
                expect(page.locator('#graded-price-status')).to_have_text('Price saved.')
                page.locator('#graded-detail-close').click()
                expect(page.locator('#graded-price-total')).to_have_text('JPY 2,550')
                before = len(calls)
                add('6091052028', 'cgc')
                expect(page.locator('#graded-dialog')).not_to_be_visible()
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(1)
                assert len(calls) == before

                # Real file input, canvas decoding and bundled jsQR; no decoder stub.
                page.locator('#graded-add').click()
                page.locator('#graded-add-price').fill('1500')
                page.locator('#graded-photo').set_input_files(str(FIXTURES / 'grading_psa_qr.png'))
                expect(page.locator('#graded-dialog')).not_to_be_visible()
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(2)
                expect(page.locator('#graded-price-total')).to_have_text('JPY 4,050')
                expect(page.locator('#graded-price-counted')).to_have_text('2/2 cards counted')
                page.locator('#currency').select_option('CNY')
                expect(page.locator('#graded-price-total')).to_have_text('CNY 202.50')
                page.locator('#currency').select_option('CAD')
                expect(page.locator('#graded-price-total')).to_have_text('CAD 40.50')
                expect(page.locator('#graded-rows tr[data-id] td.numeric .converted-price').first).to_contain_text('CAD')
                page.locator('#currency').select_option('JPY')
                expect(page.locator('#graded-price-total')).to_have_text('JPY 4,050')
                assert page.evaluate('typeof window.jsQR') == 'function'
                # Decode actual video frames without using the workstation's physical camera.
                qr = 'data:image/png;base64,' + b64encode((FIXTURES / 'grading_psa_qr.png').read_bytes()).decode()
                page.evaluate('''async (src) => {
                    const image = new Image(); image.src = src; await image.decode();
                    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 640;
                    const ctx = canvas.getContext('2d'); ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 640, 640);
                    ctx.drawImage(image, 160, 160, 320, 320);
                    navigator.mediaDevices.getUserMedia = async () => {
                        const stream = canvas.captureStream(0);
                        window.acceptanceTracks = stream.getTracks();
                        const track = stream.getVideoTracks()[0];
                        const timer = setInterval(() => {
                            if (track.readyState === 'ended') { clearInterval(timer); return; }
                            ctx.drawImage(image, 160, 160, 320, 320); track.requestFrame();
                        }, 100);
                        return stream;
                    };
                }''', qr)
                page.locator('#graded-add').click()
                page.locator('#graded-scan').click()
                expect(page.locator('#graded-scan-status')).to_contain_text('PSA 98428602 is already saved.')
                page.locator('#graded-cancel').click()
                page.wait_for_function('window.acceptanceTracks.every(track => track.readyState === "ended")')
                page.evaluate('''() => {navigator.mediaDevices.getUserMedia = async () => {
                    throw new DOMException('Denied for acceptance', 'NotAllowedError');
                };}''')
                page.locator('#graded-add').click()
                page.locator('#graded-scan').click()
                expect(page.locator('#graded-scan-status')).to_contain_text('Camera access was denied.')
                page.locator('#graded-cancel').click()
                add('047088950114', 'ars')
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(3)
                expect(page.locator('#graded-rows')).to_contain_text('047088950114')
                # ARS shows its original Japanese card name.
                expect(page.locator('#graded-rows tr[data-id]').filter(has_text='047088950114')).to_contain_text(titles['ars'])

                page.locator('#graded-query').fill('6091052028')
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(1)
                expect(page.locator('#graded-quantity-total')).to_have_text('1')
                expect(page.locator('#graded-by-grade .stat-chip-label')).to_have_text(['PRISTINE 10'])
                expect(page.locator('#graded-by-provider .stat-chip-label')).to_have_text(['CGC'])
                page.locator('#graded-reset').click()
                page.locator('#graded-provider-chips label').filter(has_text='CGC').click()
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(2)
                expect(page.locator('#graded-quantity-total')).to_have_text('2')
                expect(page.locator('#graded-price-total')).to_have_text('JPY 1,500')
                expect(page.locator('#graded-by-provider')).not_to_contain_text('CGC')
                page.locator('#graded-reset').click()
                page.locator('#graded-sort').select_option('grade_desc')
                page.locator('#graded-panel [data-view="grid"]').click()
                expect(page.locator('#graded-grid article[data-id]')).to_have_count(3)
                expect(page.locator('#graded-grid')).to_contain_text('No front image')
                # Slab images stay still: no pointer-following tilt layer.
                expect(page.locator('#graded-panel .card-image img').first).to_be_attached()
                expect(page.locator('#graded-panel .tilt')).to_have_count(0)

                # ARS English fields are folded until asked for.
                page.locator('#graded-grid article[data-id]').filter(has_text='047088950114').locator('.graded-title').click()
                english = page.locator('#graded-detail-body details.graded-english')
                expect(english).to_have_count(1)
                expect(english.locator('dl')).to_be_hidden()
                expect(page.locator('#graded-detail-body')).to_contain_text(titles['ars'])
                english.locator('summary').click()
                expect(english.locator('dl')).to_be_visible()
                expect(english).to_contain_text('Student and Researcher, Mayu Shikibe')

                # ARS has no images: upload a photo for the front; OpenCV proposes the outline.
                expect(page.locator('#graded-detail-body figure').first).to_contain_text('Click to upload the front image')
                expect(page.locator('#graded-detail-body figure').nth(1)).to_contain_text('Click to upload the back image')
                expect(page.locator('#graded-detail-body .graded-source-tag')).to_have_count(0)
                front = page.locator('#graded-detail-body .graded-image-button').first
                front.click()
                menu = page.locator('#graded-detail-body .graded-image-menu')
                expect(menu.get_by_role('menuitem')).to_have_count(2)  # no image yet: upload or scan
                menu.get_by_role('menuitem', name='Upload photo').click()
                expect(menu).to_have_count(0)
                page.locator('#graded-image-file').set_input_files(str(tmp_path / 'slab.jpg'))
                crop = page.locator('#graded-crop-dialog')
                expect(crop).to_be_visible()
                expect(page.locator('#graded-crop-title')).to_have_text('Crop the front image')
                expect(page.locator('#graded-crop-hint')).to_contain_text('Card outline found')
                expect(page.locator('#graded-crop-save')).to_be_enabled()
                handles = page.locator('#graded-crop-overlay .graded-crop-handle')
                expect(handles).to_have_count(4)
                # The proposed corners match the slab in the photo (shrunk to the browser's upload size).
                proposed = page.evaluate('''() => [...document.querySelectorAll('#graded-crop-overlay .graded-crop-handle')]
                    .map(h => [Number(h.getAttribute('cx')), Number(h.getAttribute('cy'))])''')
                assert corner_error([[x * 1200 / 1200, y] for x, y in proposed], slab_corners) < 0.04
                # Manual adjustment: drag the top-left corner, then nudge it with the keyboard.
                box = handles.first.bounding_box()
                page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                page.mouse.down(); page.mouse.move(box['x'] + 30, box['y'] + 25, steps=5); page.mouse.up()
                moved = float(handles.first.get_attribute('cx'))
                assert abs(moved - proposed[0][0]) > 5, 'dragging moves the corner'
                handles.first.focus(); page.keyboard.press('ArrowLeft')
                photos = Path(os.getenv('TCG_BROWSER_OUTPUT', 'data/graded-webui-acceptance'))
                photos.mkdir(parents=True, exist_ok=True)
                crop.screenshot(path=str(photos / 'crop-dialog.png'))
                # Optional brightness calibration replaces the photo in place and can be undone.
                original_src = page.locator('#graded-crop-image').get_attribute('src')
                corners_before = handles.evaluate_all('hs => hs.map(h => [h.getAttribute("cx"), h.getAttribute("cy")])')
                calibrate = page.locator('#graded-crop-calibrate')
                expect(calibrate).to_have_text('Calibrate brightness')
                calibrate.click()
                expect(page.locator('#graded-crop-hint')).to_contain_text('Brightness calibrated')
                expect(calibrate).to_have_attribute('aria-pressed', 'true')
                expect(calibrate).to_have_text('Undo calibration')
                calibrated_src = page.locator('#graded-crop-image').get_attribute('src')
                assert calibrated_src != original_src
                assert handles.evaluate_all('hs => hs.map(h => [h.getAttribute("cx"), h.getAttribute("cy")])') == corners_before
                crop.screenshot(path=str(photos / 'crop-calibrated.png'))
                calibrate.click()
                expect(page.locator('#graded-crop-image')).to_have_attribute('src', original_src)
                expect(calibrate).to_have_attribute('aria-pressed', 'false')
                calibrate.click()  # re-applied from memory, no new request
                expect(page.locator('#graded-crop-image')).to_have_attribute('src', calibrated_src)
                assert float(handles.first.get_attribute('cx')) < moved
                page.locator('#graded-crop-save').click()
                expect(crop).not_to_be_visible()
                expect(page.locator('#graded-detail-body figure').first).to_contain_text('User photo')
                assert len(bucket) == 1
                stored_url = next(iter(bucket))
                assert stored_url.startswith('https://cos.test/tcgtoolkit/graded/')
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(bucket[stored_url])) as stored:
                    assert stored.format == 'WEBP' and 0.5 < stored.width / stored.height < 0.7  # a slab, upright
                expect(page.locator('#graded-grid article[data-id]').filter(has_text='047088950114')
                       .locator('.graded-images img').first).to_have_attribute('src', stored_url)
                # Scanning the back takes the camera path and also starts from the detected outline.
                page.locator('#graded-detail-body .graded-image-button').nth(1).click()
                menu.get_by_role('menuitem', name='Scan card (camera)').click()
                page.locator('#graded-image-camera').set_input_files(str(tmp_path / 'card.jpg'))
                expect(page.locator('#graded-crop-hint')).to_contain_text('Card outline found')
                page.locator('#graded-crop-save').click()
                expect(crop).not_to_be_visible()
                expect(page.locator('#graded-detail-body .graded-source-tag.uploaded')).to_have_count(2)
                page.locator('#graded-detail-dialog').screenshot(path=str(photos / 'detail-uploaded.png'))
                # Removing the photo frees its stored object and brings the placeholder back.
                front.click()
                expect(menu.get_by_role('menuitem')).to_have_count(4)
                menu.get_by_role('menuitem', name='Remove my photo').click()
                expect(page.locator('#graded-detail-body .graded-source-tag.uploaded')).to_have_count(1)
                expect(page.locator('#graded-detail-body figure').first).to_contain_text('Click to upload the front image')
                expect(page.locator('#graded-detail-body figure').first.locator('.graded-source-tag')).to_have_count(0)
                assert removed == [stored_url]
                page.locator('#graded-detail-close').click()

                # A slow lookup is a card of the same size; cancelling ends the upstream request.
                add(blocking, 'psa')
                pending = page.locator('#graded-grid article.graded-pending')
                expect(pending).to_have_count(1)
                expect(page.locator('#graded-quantity-total')).to_have_text('3')
                expect(pending.locator('.loading-ring')).to_be_visible()
                expect(pending).to_contain_text('This may take up to a few minutes')
                expect(pending.locator('.graded-elapsed')).to_contain_text('Waiting 0:0')
                saved = page.locator('#graded-grid article[data-id]').first
                for part in ('', ' .graded-images'):
                    box, reference = (page.locator(f'#graded-grid article.graded-pending{part}').bounding_box(),
                                      page.locator(f'#graded-grid article[data-id]{part}').first.bounding_box())
                    assert abs(box['width'] - reference['width']) < 1, part
                    assert abs(box['height'] - reference['height']) < 1, part
                shots = Path(os.getenv('TCG_BROWSER_OUTPUT', 'data/graded-webui-acceptance'))
                shots.mkdir(parents=True, exist_ok=True)
                page.locator('#graded-grid').screenshot(path=str(shots / 'pending-grid.png'))
                page.locator('#graded-panel [data-view="list"]').click()
                expect(page.locator('#graded-rows tr.graded-pending-row .loading-ring')).to_be_visible()
                page.locator('#graded-table').screenshot(path=str(shots / 'pending-list.png'))
                page.locator('#graded-panel [data-view="grid"]').click()
                pending.get_by_role('button', name='Cancel lookup').click()
                expect(pending).to_have_count(0)
                assert released.wait(timeout=10), 'the server-side lookup was not stopped'
                expect(page.locator('#graded-status')).to_contain_text(f'Cancelled the lookup of PSA {blocking}')
                expect(page.locator('#graded-grid article[data-id]')).to_have_count(3)
                page.reload()
                expect(page.locator('#graded-grid article[data-id]')).to_have_count(3)
                expect(page.locator('#graded-grid')).not_to_contain_text(blocking)

                tile = page.locator('#graded-grid article[data-id]').filter(has_text='6091052028')
                tile.locator('.graded-title').click()
                expect(page.locator('#graded-detail-body')).to_contain_text('PRISTINE 10')
                # Provider images are marked as official.
                expect(page.locator('#graded-detail-body .graded-source-tag.official')).to_have_count(2)
                expect(page.locator('#graded-detail-body .graded-source-tag.official').first).to_have_text('Official')
                expect(page.locator('#graded-detail-price')).to_have_value('25.5')
                # Display name: optional, shown instead of the certificate name everywhere.
                expect(page.locator('#graded-detail-name')).to_have_value('')
                page.locator('#graded-detail-name').fill('  My Mayu  ')
                page.locator('#graded-name-save').click()
                expect(page.locator('#graded-name-status')).to_have_text('Name saved.')
                expect(page.locator('#graded-detail-name')).to_have_value('My Mayu')
                expect(page.locator('#graded-detail-title')).to_have_text('My Mayu')
                expect(page.locator('#graded-detail-body')).to_contain_text('Certificate name: Tsuyogaru Otona no Secret Labo Mayu')
                page.locator('#graded-detail-dialog').screenshot(path=str(shots / 'detail-price.png'))
                page.locator('#graded-detail-close').click()
                expect(tile.locator('.graded-title')).to_have_text('My Mayu')
                expect(tile.locator('.card-bottom .converted-price')).to_contain_text('CAD 25.50')
                page.locator('#graded-query').fill('my mayu')
                expect(page.locator('#graded-grid article[data-id]')).to_have_count(1)
                page.locator('#graded-query').fill('')
                tile.locator('.graded-title').click()
                # Clicking an image opens its options; the official image can still be enlarged.
                page.locator('#graded-detail-body .graded-image-button').first.click()
                page.locator('#graded-detail-body .graded-image-menu').get_by_role('menuitem', name='View larger').click()
                expect(page.locator('#image-dialog')).to_be_visible()
                page.locator('#preview-close').click()
                state['fail'] = True
                page.locator('#graded-detail-refresh').click()
                expect(page.locator('#graded-detail-body')).to_contain_text('Could not update; the saved data is unchanged')
                expect(page.locator('#graded-detail-body')).to_contain_text('PRISTINE 10')
                state['fail'] = False
                page.locator('#graded-detail-refresh').click()
                expect(page.locator('#graded-detail-body .graded-error')).to_have_count(0)
                expect(page.locator('#graded-detail-refresh')).to_be_enabled()
                page.locator('#graded-detail-close').click()

                output = Path(os.getenv('TCG_BROWSER_OUTPUT', 'data/graded-webui-acceptance'))
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / 'desktop.png'), full_page=True)
                page.locator('#language').select_option('zh-CN')
                expect(page.locator('#graded-panel h1')).to_have_text('评级卡牌')
                expect(page.locator('#graded-stats h3')).to_have_text(['按等级统计', '按评级机构统计'])
                expect(page.locator('#graded-status')).to_contain_text('CGC 6091052028')
                expect(page.locator('#graded-status')).not_to_contain_text('Updated')
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output / 'mobile.png'), full_page=True)
                page.locator('#graded-add').click()
                expect(page.locator('#graded-dialog')).to_be_visible()
                assert page.locator('#graded-dialog').bounding_box()['width'] <= 390
                page.locator('#graded-cert').fill('not-a-certificate')
                page.locator('#graded-submit').click()
                expect(page.locator('#graded-dialog-error')).not_to_be_empty()
                page.locator('#graded-cancel').click()
                page.set_viewport_size({'width': 1440, 'height': 1050})
                page.locator('#language').select_option('en')

                tile.locator('.graded-title').click()
                page.locator('#graded-detail-delete').click()
                page.locator('#graded-delete-cancel').click()
                expect(page.locator('#graded-grid article[data-id]')).to_have_count(3)
                page.locator('#graded-detail-delete').click()
                page.locator('#graded-delete-confirm').click()
                expect(page.locator('#graded-grid article[data-id]')).to_have_count(2)
                page.locator('#graded-add').click()
                page.locator('#graded-photo').set_input_files(str(FIXTURES / 'grading_pcgcn_qr.png'))
                expect(page.locator('#graded-dialog')).not_to_be_visible()
                expect(page.locator('#graded-grid article[data-id]')).to_have_count(3)
                add('https://card.pcgcard.cn/h5/index.html#/pages/f/r?type=1&query=10198854')
                expect(page.locator('#graded-grid article[data-id]')).to_have_count(3)
                pcg = page.locator('#graded-grid article[data-id]').filter(has_text='10198854')
                expect(pcg).to_contain_text('PCG-CN')
                expect(pcg.locator('.graded-title')).to_have_text(titles['pcgcn'])  # original, not the Chinese name
                pcg.locator('.graded-title').click()
                expect(page.locator('#graded-detail-body')).to_contain_text('YZ-411')
                expect(page.locator('#graded-detail-body')).to_contain_text('Surface')
                page.locator('#graded-detail-close').click()

                # Multi-select as in the card library: select all, bulk re-query, bulk delete.
                expect(page.locator('#graded-selection-bar')).to_be_hidden()
                page.locator('#graded-select').click()
                expect(page.locator('#graded-selection-bar')).to_be_visible()
                expect(page.locator('#graded-bulk-delete')).to_be_disabled()
                page.locator('#graded-select-all').check()
                expect(page.locator('#graded-selection-count')).to_have_text('3 selected')
                expect(page.locator('#graded-grid article.selected')).to_have_count(3)
                page.locator('#graded-grid').screenshot(path=str(output / 'select-grid.png'))
                page.locator('#graded-bulk-refresh').click()
                expect(page.locator('#graded-status')).to_contain_text('Looking up 3 cards again')
                expect(page.locator('#graded-selection-count')).to_have_text('0 selected')
                expect(page.locator('#graded-grid article[aria-busy]')).to_have_count(0)
                page.locator('#graded-panel [data-view="list"]').click()
                rows = page.locator('#graded-rows tr[data-id]')
                rows.nth(0).locator('input.select-box').check()
                rows.nth(1).locator('input.select-box').check()
                expect(page.locator('#graded-selection-count')).to_have_text('2 selected')
                page.locator('#graded-table').screenshot(path=str(output / 'select-list.png'))
                page.locator('#graded-bulk-delete').click()
                expect(page.locator('#graded-delete-message')).to_contain_text('2 selected')
                page.locator('#graded-delete-confirm').click()
                expect(rows).to_have_count(1)
                expect(page.locator('#graded-selection-count')).to_have_text('0 selected')
                page.locator('#graded-select').click()
                expect(page.locator('#graded-selection-bar')).to_be_hidden()
                expect(page.locator('#graded-rows .select-col').first).to_be_hidden()

                # Groups as in the card library: create, move, filter, add into, bulk move, delete.
                tabs = page.locator('#graded-groups button')
                expect(tabs).to_have_count(1)
                expect(page.locator('#graded-table th.group-col')).to_be_hidden()  # no groups yet
                page.locator('#graded-group-create').click()
                page.locator('#graded-group-name').fill('Vault')
                page.locator('#graded-group-save').click()
                expect(tabs).to_have_count(2)
                expect(tabs.nth(1)).to_have_attribute('aria-pressed', 'true')
                expect(page.locator('#graded-list-title')).to_have_text('Vault')
                expect(page.locator('#graded-empty')).to_contain_text('No cards in this group yet.')
                page.locator('#graded-add').click()
                expect(page.locator('#graded-add-group')).to_have_value(re.compile('.+'))  # defaults to the open tab
                page.locator('#graded-cancel').click()
                tabs.nth(0).click()
                left = page.locator('#graded-rows tr[data-id]')
                expect(left).to_have_count(1)
                left.get_by_role('button', name='Move to group').click()
                page.locator('#graded-move-group').select_option(label='Vault')
                page.locator('#graded-move-save').click()
                expect(page.locator('#graded-move-dialog')).to_be_hidden()
                expect(left.locator('td.group-col')).to_have_text('Vault')
                expect(tabs.nth(1)).to_contain_text('1')
                tabs.nth(1).click()
                expect(left).to_have_count(1)
                page.locator('#graded-panel').screenshot(path=str(output / 'groups.png'))
                page.reload()
                expect(page.locator('#graded-rows tr[data-id] td.group-col')).to_have_text('Vault')  # saved
                page.locator('#graded-groups button').nth(0).click()
                page.locator('#graded-select').click()
                page.locator('#graded-select-all').check()
                page.locator('#graded-bulk-move').click()
                page.locator('#graded-move-group').select_option('')
                page.locator('#graded-move-save').click()
                expect(page.locator('#graded-rows tr[data-id] td.group-col')).to_have_text('—')
                page.locator('#graded-select').click()
                page.locator('#graded-groups button').nth(1).click()
                page.locator('#graded-group-delete').click()
                expect(page.locator('#graded-group-delete-message')).to_contain_text('Vault')
                page.locator('#graded-group-delete-confirm').click()
                expect(page.locator('#graded-groups button')).to_have_count(1)
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(1)
                page.locator('#account-link').click()
                page.locator('#logout').click()
                expect(page.locator('#auth-panel')).to_be_visible()
                expect(page.locator('#graded-grid article[data-id]')).to_have_count(0)
                assert errors == []
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()
