"""PSA webpage lookup through the real proxy-go client and an in-memory gateway."""
import base64
import json
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi import HTTPException
from fastapi.testclient import TestClient

from auth_helpers import register_user
import tcg_toolkit.grading as module
from tcg_toolkit.web import app


ORIGIN = 'https://testserver'
NUMBER = '98428602'
URL = f'/api/grading/psa/certificates/{NUMBER}'
HTML = (Path(__file__).parent / 'fixtures/psa_certificate.html').read_text(encoding='utf-8')
MISSING = (Path(__file__).parent / 'fixtures/psa_not_found.html').read_text(encoding='utf-8')
TITLE = ('2024 LYCEE OVERTURE JAPANESE VER. NEXTON SUMMER SPECIAL PACK #3955 '
         'CHITOSE TUTAMACHI NEX-SUMMER SP PACK-SP SIG')
IMAGES = [
    {'url': 'https://d1htnxwo4o0jhw.cloudfront.net/cert/166149476/small/bhO1GQ4Ke0KxqhnfoExo3A.jpg',
     'alt': 'Cert image 1'},
    {'url': 'https://d1htnxwo4o0jhw.cloudfront.net/cert/166149476/small/fVpAFc1PAkWuxmpyJJUOuw.jpg',
     'alt': 'Cert image 2'},
]


def gateway_response(html=HTML, *, target_status=200, content_type='text/html; charset=utf-8'):
    return httpx.Response(200, json={
        'request_id': 'psa-request-1', 'country': 'US', 'status_code': target_status,
        'headers': {'content-type': content_type},
        'body_base64': base64.b64encode(html.encode()).decode(),
        'proxy_traffic': {'upload_bytes': 100, 'download_bytes': 900, 'total_bytes': 1000,
                          'attempts': [{'attempt_id': 'attempt-1', 'upload_bytes': 100,
                                        'download_bytes': 900, 'total_bytes': 1000}]},
    })


def test_parse_real_fixture_preserves_fields_and_only_certificate_images():
    extra = HTML.replace('</dl>', '<div><dt>Future field</dt><dd>kept</dd></div></dl>', 1)
    extra = extra.replace('</main>', '<img alt="PSA logo" src="https://images.test/logo.png">'
                          '<img alt="Sales history" src="https://images.test/sale.jpg"></main>', 1)
    result = module.parse_psa(extra, NUMBER)
    assert result['provider'] == 'psa'
    assert result['cert_number'] == NUMBER
    assert result['source_url'] == f'https://www.psacard.com/en-CA/cert/{NUMBER}/psa'
    assert result['images'] == IMAGES
    cert = result['certificate']
    assert cert['title'] == TITLE
    assert cert['grade'] == 'GEM MT 10'
    assert cert['fields']['Cert Number'] == NUMBER
    assert cert['fields']['Subject'] == 'CHITOSE TUTAMACHI'
    assert cert['fields']['Card Number'] == '3955'
    assert cert['fields']['Year'] == '2024'
    assert cert['fields']['Label Type'] == 'w/ Fugitive Ink Technology'
    assert cert['fields']['Future field'] == 'kept'
    assert cert['population'] == {'at_grade': 254, 'higher': 0}


@pytest.mark.parametrize('selector', ['main', 'h1', 'dl'])
def test_missing_required_structure_is_unreadable(selector):
    soup = BeautifulSoup(HTML, 'html.parser')
    soup.select_one(selector).decompose()
    with pytest.raises(HTTPException) as caught:
        module.parse_psa(str(soup), NUMBER)
    assert (caught.value.status_code, caught.value.detail) == (502, 'psa_unreadable')


def test_absent_population_remains_unknown():
    soup = BeautifulSoup(HTML, 'html.parser')
    for group in soup.select('[role="group"][aria-labelledby]'):
        group.decompose()
    result = module.parse_psa(str(soup), NUMBER)
    assert result['certificate']['population'] == {'at_grade': None, 'higher': None}
    assert result['certificate']['grade'] == 'GEM MT 10'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    monkeypatch.setenv('PROXY_GO_API', 'http://proxy-go.test/v1/fetch')
    monkeypatch.setenv('PROXY_GO_API_KEY', 'test-proxy-secret')
    monkeypatch.setenv('TCG_PSA_API_TOKEN', 'must-not-send')
    real_client = module.ProxyGoClient
    calls, replies = [], []

    def handle(request):
        calls.append(request)
        assert request.method == 'POST'
        assert str(request.url) == 'http://proxy-go.test/v1/fetch'
        assert request.headers['authorization'] == 'Bearer test-proxy-secret'
        assert 'must-not-send' not in str(request.headers)
        assert b'must-not-send' not in request.content
        reply = replies.pop(0) if replies else gateway_response()
        if isinstance(reply, Exception):
            raise reply
        return reply

    def factory():
        return real_client(transport=httpx.MockTransport(handle))

    monkeypatch.setattr(module, 'ProxyGoClient', factory)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as browser:
        browser.headers['Authorization'] = 'Bearer ' + register_user(browser).json()['token']
        yield browser, calls, replies


def test_complete_lookup_uses_one_proxy_fetch(client, monkeypatch, caplog):
    browser, calls, _ = client
    with caplog.at_level('INFO', logger=module.__name__):
        response = browser.get('/api/grading/psa/certificates/%20' + NUMBER + '%20')
    assert response.status_code == 200
    assert response.json() == module.parse_psa(HTML, NUMBER)
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1
    assert json.loads(calls[0].content) == {
        'url': f'https://www.psacard.com/en-CA/cert/{NUMBER}/psa', 'country': 'US', 'method': 'GET',
    }
    assert calls[0].extensions['timeout'] == {'connect': 5, 'read': 220, 'write': 220, 'pool': 220}
    assert 'request_id=psa-request-1' in caplog.text
    assert 'proxy_total_bytes=1000' in caplog.text
    assert 'test-proxy-secret' not in caplog.text
    # Queries stay fresh and the old PSA API token is no longer required.
    monkeypatch.delenv('TCG_PSA_API_TOKEN')
    assert browser.get(URL).status_code == 200
    assert len(calls) == 2


def test_leading_zero_certificate_number_is_preserved(client):
    browser, calls, replies = client
    number = '00123456'
    replies.append(gateway_response(HTML.replace(NUMBER, number)))
    response = browser.get('/api/grading/psa/certificates/' + number)
    assert response.status_code == 200
    assert response.json()['cert_number'] == number
    assert response.json()['certificate']['fields']['Cert Number'] == number
    assert json.loads(calls[0].content)['url'].endswith('/' + number + '/psa')


@pytest.mark.parametrize('path', [
    '/api/grading/bgs/certificates/123',
    '/api/grading/psa/certificates/%20',
    '/api/grading/psa/certificates/123abc',
    '/api/grading/psa/certificates/１２３',
    '/api/grading/psa/certificates/' + '1' * 33,
])
def test_invalid_input_never_fetches(client, path):
    browser, calls, _ = client
    assert browser.get(path).status_code == 422
    assert not calls


def test_authentication_required(client):
    browser, calls, _ = client
    browser.headers.pop('Authorization')
    assert browser.get(URL).status_code == 401
    assert not calls


@pytest.mark.parametrize('variable', ['PROXY_GO_API', 'PROXY_GO_API_KEY'])
def test_missing_proxy_configuration(client, monkeypatch, variable):
    browser, calls, _ = client
    monkeypatch.setenv(variable, '')
    response = browser.get(URL)
    assert response.status_code == 503
    assert response.json() == {'detail': 'psa_not_configured'}
    assert response.headers['cache-control'] == 'no-store'
    assert not calls


def test_no_certificate_images_returns_empty_list(client):
    browser, _, replies = client
    soup = BeautifulSoup(HTML, 'html.parser')
    for image in soup.select('img[alt^="Cert image "]'):
        image.decompose()
    replies.append(gateway_response(str(soup)))
    response = browser.get(URL)
    assert response.status_code == 200
    assert response.json()['images'] == []
    assert response.json()['certificate']['grade'] == 'GEM MT 10'


@pytest.mark.parametrize('target_status,detail', [
    (403, 'psa_unavailable'), (302, 'psa_unavailable'), (429, 'psa_rate_limited'),
])
def test_target_status_is_distinct_from_successful_gateway(client, target_status, detail):
    browser, calls, replies = client
    replies.append(gateway_response(target_status=target_status))
    response = browser.get(URL)
    assert response.status_code == 502
    assert response.json() == {'detail': detail}
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1


@pytest.mark.parametrize('gateway_status,status,detail', [
    (401, 502, 'psa_authentication_failed'), (403, 502, 'psa_authentication_failed'),
    (429, 502, 'psa_rate_limited'), (504, 504, 'psa_timeout'), (502, 502, 'psa_unavailable'),
])
def test_gateway_errors_preserve_safe_diagnostics(client, caplog, gateway_status, status, detail):
    browser, calls, replies = client
    replies.append(httpx.Response(gateway_status, json={'detail': 'private gateway detail'}, headers={
        'X-Request-ID': 'failed-request', 'X-Proxy-Total-Bytes': '12345', 'X-Proxy-Attempts': '3',
    }))
    response = browser.get(URL)
    assert response.status_code == status
    assert response.json() == {'detail': detail}
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1
    assert 'request_id=failed-request' in caplog.text
    assert 'proxy_total_bytes=12345' in caplog.text
    assert 'proxy_attempts=3' in caplog.text
    assert 'private gateway detail' not in caplog.text
    assert 'test-proxy-secret' not in caplog.text


@pytest.mark.parametrize('error,status,detail', [
    (httpx.ReadTimeout('private detail'), 504, 'psa_timeout'),
    (httpx.ConnectError('private detail'), 502, 'psa_unavailable'),
])
def test_network_errors(client, error, status, detail):
    browser, calls, replies = client
    replies.append(error)
    response = browser.get(URL)
    assert response.status_code == status
    assert response.json() == {'detail': detail}
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1


@pytest.mark.parametrize('target_status', [200, 404])
def test_nextjs_not_found_page(client, target_status):
    browser, calls, replies = client
    replies.append(gateway_response(MISSING, target_status=target_status))
    response = browser.get(URL)
    assert response.status_code == 404
    assert response.json() == {'detail': 'grading_certificate_not_found'}
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1


@pytest.mark.parametrize('html,content_type', [
    ('<main>private challenge</main>', 'text/html'),
    (HTML.replace(NUMBER, '99999999'), 'text/html'),
    (HTML, 'application/json'),
])
def test_unreadable_target_content(client, html, content_type):
    browser, calls, replies = client
    replies.append(gateway_response(html, content_type=content_type))
    response = browser.get(URL)
    assert response.status_code == 502
    assert response.json() == {'detail': 'psa_unreadable'}
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1
