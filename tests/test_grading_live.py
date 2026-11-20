"""Opt-in live HTTP lookups: PSA/CGC through Zyte, ARS directly."""
import json
import logging
import os
from pathlib import Path
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from auth_helpers import install_mailbox, register_user
from tcg_toolkit.web import app


@pytest.mark.parametrize('provider,number,source_url', [
    pytest.param('psa', '98428602', 'https://www.psacard.com/en-CA/cert/98428602/psa',
                 marks=pytest.mark.skipif(os.getenv('TCG_LIVE_PSA_TEST') != '1',
                                         reason='Set TCG_LIVE_PSA_TEST=1 for live PSA lookup.'),
                 id='psa'),
    pytest.param('cgc', '6091052028', 'https://www.cgccards.com/certlookup/6091052028/',
                 marks=pytest.mark.skipif(os.getenv('TCG_LIVE_CGC_TEST') != '1',
                                         reason='Set TCG_LIVE_CGC_TEST=1 for live CGC lookup.'),
                 id='cgc'),
    pytest.param('ars', '047088950114', 'https://ars-grading.com/grading/searchNumber?id=047088950114',
                 marks=pytest.mark.skipif(os.getenv('TCG_LIVE_ARS_TEST') != '1',
                                         reason='Set TCG_LIVE_ARS_TEST=1 for live ARS lookup.'),
                 id='ars'),
])
def test_certificate_lookup_over_http(monkeypatch, caplog, provider, number, source_url):
    certificate_path = f'/api/grading/{provider}/certificates/{number}'
    monkeypatch.setenv('TCG_SUBSCRIPTIONS_ENABLED', 'false')
    monkeypatch.setenv('TCG_AVATAR_COS_URL', '')
    caplog.set_level(logging.INFO, logger='tcg_toolkit.grading.adapters')

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
        monkeypatch.setenv('TCG_TRUSTED_ORIGINS', origin)
        server = uvicorn.Server(uvicorn.Config(
            app, log_config=None, log_level='warning', access_log=False,
            lifespan='on', timeout_graceful_shutdown=5,
        ))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started, 'TCG Toolkit HTTP server failed to start'

            # Prepare a valid test session; only email delivery is replaced.
            # The measured flow starts at the certificate HTTP endpoint.
            install_mailbox()
            with httpx.Client(base_url=origin, headers={'Origin': origin}, trust_env=False,
                              timeout=httpx.Timeout(240, connect=5)) as browser:
                registered = register_user(browser, username=f'{provider}_live', password='password123')
                assert registered.status_code == 201
                browser.headers['Authorization'] = 'Bearer ' + registered.json()['token']

                started = time.monotonic()
                response = browser.get(certificate_path)
                elapsed = time.monotonic() - started
                assert response.status_code == 200, response.text
                assert response.headers['cache-control'] == 'no-store'
                result = response.json()
                assert result['provider'] == provider
                assert result['cert_number'] == number
                assert result['source_url'] == source_url
                certificate = result['certificate']
                assert certificate['fields']
                assert certificate['grade']
                if provider != 'ars':
                    assert result['images']
                if provider == 'psa':
                    assert certificate['fields']['Cert Number'] == number
                    assert certificate['fields']['Subject'] == 'CHITOSE TUTAMACHI'
                    assert certificate['fields']['Card Number'] == '3955'
                    assert certificate['fields']['Year'] == '2024'
                    assert certificate['grade'] == 'GEM MT 10'
                    assert certificate['title']
                    assert len(result['images']) == 2
                elif provider == 'cgc':
                    assert certificate['fields']['Cert #'] == number
                    assert certificate['fields']['Card Name'] == 'Tsuyogaru Otona no Secret Labo Mayu'
                    assert certificate['fields']['Game'] == 'Weiss Schwarz Rose'
                    assert certificate['fields']['Card Number'] == 'R01-026SP'
                    assert certificate['fields']['Year'] == '2025'
                    assert certificate['grade'] == 'PRISTINE 10'
                    assert certificate['title'] == certificate['fields']['Card Name']
                    assert [image['alt'] for image in result['images']] == ['Obverse', 'Reverse']
                    assert all('TN_' not in image['url'] for image in result['images'])
                else:
                    assert certificate['fields']['Card Name'] == 'Student and Researcher, Mayu Shikibe'
                    assert certificate['grade'] == '10'
                    assert result['images'] == []
                for image in result['images']:
                    image_url = httpx.URL(image['url'])
                    assert image_url.scheme == 'https' and image_url.host
                    assert image['alt']

                proxy_messages = [record.getMessage() for record in caplog.records
                                  if record.name == 'tcg_toolkit.grading.adapters'
                                  and record.getMessage().startswith(
                                      f'{provider.upper()} zyte target_status=')]
                if provider == 'ars':
                    assert not proxy_messages
                else:
                    assert len(proxy_messages) == 1
                    assert 'target_status=200' in proxy_messages[0]

            output = os.getenv(f'TCG_LIVE_{provider.upper()}_OUTPUT')
            if output:
                destination = Path(output)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({
                'flow': f'GET {certificate_path} -> {"direct" if provider == "ars" else "zyte"} -> {provider.upper()} -> parsed certificate',
                'status': response.status_code,
                'provider': result['provider'],
                'cert_number': result['cert_number'],
                'image_count': len(result['images']),
                'lookup_seconds': round(elapsed, 2),
            }))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), 'TCG Toolkit HTTP server failed to stop'
