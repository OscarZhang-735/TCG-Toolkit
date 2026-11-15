"""Opt-in Chromium acceptance: real WebUI/API/PostgreSQL, fixture providers."""
from dataclasses import replace
from base64 import b64encode
from pathlib import Path
import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import HTTPException

from auth_helpers import install_mailbox, register_user
from tcg_toolkit import grading
from tcg_toolkit.web import app

pytestmark = pytest.mark.skipif(os.getenv('TCG_BROWSER_TEST') != '1', reason='Set TCG_BROWSER_TEST=1 for Chromium acceptance.')
FIXTURES = Path(__file__).parent / 'fixtures'


def test_graded_webui(monkeypatch):
    from playwright.sync_api import sync_playwright, expect

    monkeypatch.setenv('TCG_AVATAR_COS_URL', '')
    state = {'fail': False}
    calls = []
    for provider, number in [('psa', '98428602'), ('cgc', '6091052028'), ('ars', '047088950114')]:
        parsed = getattr(grading, f'parse_{provider}')((FIXTURES / f'{provider}_certificate.html').read_text(encoding='utf-8'), number)

        def fetch(requested, result=parsed):
            calls.append((result['provider'], requested))
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
                page.goto(origin)
                page.evaluate('(token) => localStorage.setItem("tcg.session", token)', token)
                page.goto(origin + '/#graded')
                page.reload()  # a fragment-only navigation does not restore the newly set session
                expect(page.locator('#graded-panel')).to_be_visible()
                expect(page.locator('#graded-add')).to_be_enabled()
                expect(page.locator('#graded-empty')).to_be_visible()
                expect(page.locator('a[href="#graded"]')).to_have_attribute('aria-current', 'page')

                def add(value, provider=None):
                    page.locator('#graded-add').click()
                    if provider:
                        page.locator('#graded-provider').select_option(provider)
                    page.locator('#graded-cert').fill(value)
                    page.locator('#graded-submit').click()

                # Official links pick the provider; the saved row survives a reload.
                add('https://www.cgccards.com/certlookup/6091052028/10_0/')
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(1)
                expect(page.locator('#graded-rows')).to_contain_text('PRISTINE 10')
                page.reload()
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(1)
                before = len(calls)
                add('6091052028', 'cgc')
                expect(page.locator('#graded-dialog')).not_to_be_visible()
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(1)
                assert len(calls) == before

                # Real file input, canvas decoding and bundled jsQR; no decoder stub.
                page.locator('#graded-add').click()
                page.locator('#graded-photo').set_input_files(str(FIXTURES / 'grading_psa_qr.png'))
                expect(page.locator('#graded-scan-status')).to_contain_text('Queued PSA 98428602')
                page.locator('#graded-cancel').click()
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(2)
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

                page.locator('#graded-query').fill('6091052028')
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(1)
                page.locator('#graded-reset').click()
                page.locator('#graded-provider-chips label').filter(has_text='CGC').click()
                expect(page.locator('#graded-rows tr[data-id]')).to_have_count(2)
                page.locator('#graded-reset').click()
                page.locator('#graded-sort').select_option('grade_desc')
                page.locator('#graded-panel [data-view="grid"]').click()
                expect(page.locator('#graded-grid article[data-id]')).to_have_count(3)
                expect(page.locator('#graded-grid')).to_contain_text('No front image')

                tile = page.locator('#graded-grid article[data-id]').filter(has_text='6091052028')
                tile.locator('.graded-title').click()
                expect(page.locator('#graded-detail-body')).to_contain_text('PRISTINE 10')
                page.locator('#graded-detail-body .card-image').first.click()
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
