"""PSA API contract tests; all upstream requests use an in-memory transport."""
import httpx
import pytest
from fastapi.testclient import TestClient

from auth_helpers import register_user
import tcg_toolkit.grading as module
from tcg_toolkit.web import app


ORIGIN = 'https://testserver'
NUMBER = '00123456'
URL = f'/api/grading/psa/certificates/{NUMBER}'
CERT = {'IsValidRequest': True, 'ServerMessage': 'Request successful',
        'PSACert': {'CertNumber': NUMBER, 'CardGrade': 'GEM MT 10',
                    'PopulationHigher': 0, 'ExtraField': {'nested': True}},
        'DNACert': None, 'FutureField': [1, 2]}
# Synthetic image payload: the API does not publish a detailed image schema.
IMAGES = [{'ImageURL': 'https://images.example.test/card.jpg', 'ExtraField': 'kept'}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    monkeypatch.setenv('TCG_PSA_API_TOKEN', 'test-psa-secret')
    real_client = httpx.Client
    calls = []
    replies = [CERT, IMAGES]

    def handle(request):
        calls.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False
        assert kwargs['trust_env'] is False
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    # Replace this module's HTTPX binding, not TestClient's shared httpx.Client.
    from types import SimpleNamespace
    monkeypatch.setattr(module, 'httpx', SimpleNamespace(
        Client=factory, TimeoutException=httpx.TimeoutException, HTTPError=httpx.HTTPError))
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as browser:
        browser.headers['Authorization'] = 'Bearer ' + register_user(browser).json()['token']
        yield browser, calls, replies


def test_complete_lookup(client):
    browser, calls, replies = client
    response = browser.get('/api/grading/psa/certificates/%20' + NUMBER + '%20')
    assert response.status_code == 200
    assert response.json() == {'provider': 'psa', 'cert_number': NUMBER,
        'source_url': f'https://www.psacard.com/cert/{NUMBER}/',
        'certificate': CERT, 'images': IMAGES}
    assert response.headers['cache-control'] == 'no-store'
    assert [str(call.url) for call in calls] == [
        f'{module.PSA_API_URL}/GetByCertNumber/{NUMBER}',
        f'{module.PSA_API_URL}/GetImagesByCertNumber/{NUMBER}']
    for call in calls:
        assert call.headers['authorization'] == 'bearer test-psa-secret'
        assert call.headers['accept'] == 'application/json'
        assert set(call.extensions['timeout'].values()) == {20}
    # Queries are fresh, not cached.
    replies.extend([CERT, IMAGES])
    assert browser.get(URL).status_code == 200
    assert len(calls) == 4


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


def test_missing_configuration(client, monkeypatch):
    browser, calls, _ = client
    monkeypatch.delenv('TCG_PSA_API_TOKEN')
    response = browser.get(URL)
    assert response.status_code == 503
    assert response.json() == {'detail': 'psa_not_configured'}
    assert not calls


@pytest.mark.parametrize('payload', [[], {'IsValidRequest': True, 'ServerMessage': 'No data found'}])
def test_no_images_keeps_upstream_result(client, payload):
    browser, _, replies = client
    replies[:] = [CERT, payload]
    response = browser.get(URL)
    assert response.status_code == 200
    assert response.json()['images'] == payload


@pytest.mark.parametrize('stage', [0, 1])
@pytest.mark.parametrize('reply,status,detail', [
    (httpx.Response(401, text='test-psa-secret'), 502, 'psa_authentication_failed'),
    (httpx.Response(403), 502, 'psa_authentication_failed'),
    (httpx.Response(429), 502, 'psa_rate_limited'),
    (httpx.Response(500), 502, 'psa_unavailable'),
    (httpx.Response(404), 502, 'psa_unavailable'),
    (httpx.Response(302, headers={'Location': 'https://example.test'}), 502, 'psa_unavailable'),
    (httpx.Response(204), 502, 'psa_unreadable'),
    (httpx.Response(200, text='<html>challenge</html>'), 502, 'psa_unreadable'),
    (httpx.ReadTimeout('secret upstream detail'), 504, 'psa_timeout'),
    (httpx.ConnectError('secret upstream detail'), 502, 'psa_unavailable'),
    ({'IsValidRequest': False, 'ServerMessage': 'Invalid CertNo'}, 502, 'psa_request_rejected'),
    ({'IsValidRequest': True, 'ServerMessage': 'Unexpected failure'}, 502, 'psa_request_rejected'),
    ('not an object', 502, 'psa_unreadable'),
    (None, 502, 'psa_unreadable'),
])
def test_upstream_errors(client, stage, reply, status, detail):
    browser, calls, replies = client
    replies[:] = ([CERT] if stage else []) + [reply]
    response = browser.get(URL)
    assert response.status_code == status
    assert response.json() == {'detail': detail}
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == stage + 1  # no retry or image query after certificate failure


def test_no_certificate(client):
    browser, calls, replies = client
    replies[:] = [{'IsValidRequest': True, 'ServerMessage': 'No data found'}]
    response = browser.get(URL)
    assert response.status_code == 404
    assert response.json() == {'detail': 'grading_certificate_not_found'}
    assert len(calls) == 1


@pytest.mark.parametrize('payload', [{}, [], {'IsValidRequest': True},
    {'PSACert': {'CertNumber': '99999999'}}, {'PSACert': None, 'DNACert': None}])
def test_missing_or_mismatched_certificate_is_not_success(client, payload):
    browser, calls, replies = client
    replies[:] = [payload]
    assert browser.get(URL).json() == {'detail': 'psa_unreadable'}
    assert len(calls) == 1


def test_dna_certificate_and_object_image_response(client):
    browser, _, replies = client
    cert = {'DNACert': {'CertNumber': NUMBER, 'Notes': 'Preserve all fields'}}
    images = {'Images': IMAGES, 'FutureField': True}
    replies[:] = [cert, images]
    response = browser.get(URL)
    assert response.status_code == 200
    assert response.json()['certificate'] == cert
    assert response.json()['images'] == images
