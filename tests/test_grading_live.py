"""Opt-in HTTP end-to-end PSA lookup through the configured real proxy-go."""
import json
import logging
import os
from pathlib import Path
import re
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from auth_helpers import install_mailbox, register_user
from tcg_toolkit.web import app


pytestmark = pytest.mark.skipif(
    os.getenv('TCG_LIVE_PSA_TEST') != '1',
    reason='Set TCG_LIVE_PSA_TEST=1 to use the live proxy-go and PSA services.',
)

NUMBER = '98428602'
CERTIFICATE_PATH = f'/api/grading/psa/certificates/{NUMBER}'


def test_psa_lookup_over_http(monkeypatch, caplog):
    monkeypatch.setenv('TCG_SUBSCRIPTIONS_ENABLED', 'false')
    monkeypatch.setenv('TCG_AVATAR_COS_URL', '')
    caplog.set_level(logging.INFO, logger='tcg_toolkit.grading')

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
            # The measured flow starts at the PSA certificate HTTP endpoint.
            install_mailbox()
            with httpx.Client(base_url=origin, headers={'Origin': origin}, trust_env=False,
                              timeout=httpx.Timeout(240, connect=5)) as browser:
                registered = register_user(browser, username='psa_live', password='password123')
                assert registered.status_code == 201
                browser.headers['Authorization'] = 'Bearer ' + registered.json()['token']

                started = time.monotonic()
                response = browser.get(CERTIFICATE_PATH)
                elapsed = time.monotonic() - started
                assert response.status_code == 200, response.text
                assert response.headers['cache-control'] == 'no-store'
                result = response.json()
                assert result['provider'] == 'psa'
                assert result['cert_number'] == NUMBER
                certificate = result['certificate']
                assert certificate['fields']['Cert Number'] == NUMBER
                assert certificate['fields']['Subject'] == 'CHITOSE TUTAMACHI'
                assert certificate['fields']['Card Number'] == '3955'
                assert certificate['fields']['Year'] == '2024'
                assert certificate['grade'] == 'GEM MT 10'
                assert certificate['title']
                assert len(result['images']) == 2
                for image in result['images']:
                    image_url = httpx.URL(image['url'])
                    assert image_url.scheme == 'https' and image_url.host
                    assert image['alt']

                proxy_messages = [record.getMessage() for record in caplog.records
                                  if record.name == 'tcg_toolkit.grading'
                                  and record.getMessage().startswith('PSA proxy-go request_id=')]
                assert len(proxy_messages) == 1
                request_id = re.search(r'request_id=(\S+)', proxy_messages[0])
                assert request_id and request_id.group(1) != 'None'
                assert 'target_status=200' in proxy_messages[0]

            output = os.getenv('TCG_LIVE_PSA_OUTPUT')
            if output:
                destination = Path(output)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({
                'flow': f'GET {CERTIFICATE_PATH} -> proxy-go -> PSA -> parsed certificate',
                'status': response.status_code,
                'provider': result['provider'],
                'cert_number': result['cert_number'],
                'image_count': len(result['images']),
                'proxy_request_id': request_id.group(1),
                'lookup_seconds': round(elapsed, 2),
            }))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), 'TCG Toolkit HTTP server failed to stop'
