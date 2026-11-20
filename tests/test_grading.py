"""PSA certificate lookup through Zyte using recorded responses."""
import base64
from pathlib import Path

import httpx
import pytest

from test_cards import client

NUMBER = '98428602'
URL = f'/api/grading/psa/certificates/{NUMBER}'
SOURCE = f'https://www.psacard.com/en-CA/cert/{NUMBER}/psa'
FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv('ZYTE_API_KEY', 'test-proxy-secret')

    def serve(html):
        def post(self, endpoint, *, auth, json):
            assert endpoint == 'https://api.zyte.com/v1/extract'
            assert auth == ('test-proxy-secret', '')
            assert json['url'] == SOURCE
            return httpx.Response(200, json={
                'url': SOURCE, 'statusCode': 200,
                'httpResponseHeaders': [{'name': 'content-type', 'value': 'text/html; charset=utf-8'}],
                'httpResponseBody': base64.b64encode(html.encode()).decode(),
            })
        monkeypatch.setattr(httpx.Client, 'post', post)

    return serve


def test_authenticated_lookup(client, gateway):
    gateway((FIXTURES / 'psa_certificate.html').read_text(encoding='utf-8'))
    response = client.get(URL)
    assert response.status_code == 200
    result = response.json()
    assert (result['provider'], result['cert_number'], result['source_url']) == ('psa', NUMBER, SOURCE)
    cert = result['certificate']
    assert cert['grade'] == 'GEM MT 10'
    assert cert['fields']['Card Number'] == '3955'
    assert cert['population'] == {'at_grade': 254, 'higher': 0}
    assert [image['side'] for image in result['images']] == ['front', 'back']
    client.headers.pop('Authorization')
    assert client.get(URL).status_code == 401


@pytest.mark.parametrize('fixture,expected_status,detail', [
    ('psa_not_found.html', 404, 'grading_certificate_not_found'),
    ('psa_certificate.html', 502, 'psa_unreadable'),
])
def test_missing_and_mismatched_certificates(client, gateway, fixture, expected_status, detail):
    html = (FIXTURES / fixture).read_text(encoding='utf-8')
    gateway(html.replace(NUMBER, '99999999'))
    response = client.get(URL)
    assert response.status_code == expected_status
    assert response.json() == {'detail': detail}
