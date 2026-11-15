"""CGC certificate parsing and proxy-go routing with recorded HTML."""
import base64
import json
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi import HTTPException
from fastapi.testclient import TestClient

from auth_helpers import register_user
from tcg_toolkit import grading as module
from tcg_toolkit.web import app


NUMBER = '6091052028'
HTML = (Path(__file__).parent / 'fixtures/cgc_certificate.html').read_text(encoding='utf-8')
URL = '/api/grading/cgc/certificates/' + NUMBER
SOURCE_URL = f'https://www.cgccards.com/certlookup/{NUMBER}/'
IMAGE_BASE = ('https://ccg-imaging-cgc-tradingcards-production.s3.amazonaws.com/'
              '17635520-875d-4082-ab53-cd12c96ea49a/')
# Originals come from the links; the page's TN_ thumbnails are kept for grids.
IMAGES = [
    {'url': IMAGE_BASE + 'CAR6091052-028_OBV.jpg', 'alt': 'Obverse', 'side': 'front',
     'thumbnail_url': IMAGE_BASE + 'TN_CAR6091052-028_OBV.jpg'},
    {'url': IMAGE_BASE + 'CAR6091052-028_REV.jpg', 'alt': 'Reverse', 'side': 'back',
     'thumbnail_url': IMAGE_BASE + 'TN_CAR6091052-028_REV.jpg'},
]
FIELDS = {
    'Cert #': NUMBER, 'Card Name': 'Tsuyogaru Otona no Secret Labo Mayu',
    'Game': 'Weiss Schwarz Rose', 'Year': '2025', 'Language': 'Japanese',
    'Card Set': 'Yuzu Soft', 'Card Number': 'R01-026SP', 'Rarity': 'Special',
    'Grade': 'PRISTINE 10', 'Grade Date': '2025-11-19',
}


def gateway_response(html=HTML, *, target_status=200):
    return httpx.Response(200, json={
        'request_id': 'cgc-request-1', 'country': 'US', 'status_code': target_status,
        'headers': {'content-type': 'text/html; charset=utf-8'},
        'body_base64': base64.b64encode(html.encode()).decode(),
        'proxy_traffic': None,
    })


def test_parse_real_certificate_ignores_hidden_errors_and_uses_original_images():
    # A successful page also carries error templates and unrendered population.
    assert 'This item cannot be found.' in HTML
    assert 'Your search activity has exceeded our limits.' in HTML
    result = module.parse_cgc(HTML, NUMBER)
    assert result == {
        'provider': 'cgc', 'cert_number': NUMBER, 'source_url': SOURCE_URL,
        'certificate': {'title': FIELDS['Card Name'], 'grade': 'PRISTINE 10',
                        'fields': FIELDS, 'population': {'at_grade': None, 'higher': None}},
        'images': IMAGES,
    }
    assert all('/TN_' not in image['url'] for image in result['images'])


def test_additional_fields_and_image_scope():
    soup = BeautifulSoup(HTML, 'html.parser')
    label, value = soup.new_tag('dt'), soup.new_tag('dd')
    label.string, value.string = 'Future field', 'kept'
    fields = soup.select_one('.certlookup-intro dl')
    fields.extend([label, value])
    unrelated = BeautifulSoup(
        '<a href="https://images.test/ad.jpg"><img title="Advert" src="https://images.test/thumb.jpg"></a>',
        'html.parser')
    soup.select_one('.certlookup-intro').append(unrelated)
    result = module.parse_cgc(str(soup), NUMBER)
    assert result['certificate']['fields']['Future field'] == 'kept'
    assert result['images'] == IMAGES


@pytest.mark.parametrize('field', ['Cert #', 'Card Name', 'Grade'])
def test_missing_required_field_is_unreadable(field):
    soup = BeautifulSoup(HTML, 'html.parser')
    label = next(label for label in soup.select('.certlookup-intro dl dt')
                 if label.get_text(' ', strip=True) == field)
    label.find_next_sibling('dd').decompose()
    with pytest.raises(HTTPException) as caught:
        module.parse_cgc(str(soup), NUMBER)
    assert (caught.value.status_code, caught.value.detail) == (502, 'cgc_unreadable')


@pytest.fixture
def client(monkeypatch):
    origin = 'https://testserver'
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', origin)
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
        assert b'must-not-send' not in request.content
        reply = replies.pop(0) if replies else gateway_response()
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(module, 'ProxyGoClient',
                        lambda: real_client(transport=httpx.MockTransport(handle)))
    with TestClient(app, base_url=origin, headers={'Origin': origin}) as browser:
        browser.headers['Authorization'] = 'Bearer ' + register_user(browser).json()['token']
        yield browser, calls, replies


def test_authenticated_lookup_uses_proxy_go(client):
    browser, calls, _ = client
    response = browser.get('/api/grading/cgc/certificates/%20' + NUMBER + '%20')
    assert response.status_code == 200
    assert response.json() == module.parse_cgc(HTML, NUMBER)
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1
    assert json.loads(calls[0].content) == {'url': SOURCE_URL, 'country': 'US', 'method': 'GET'}
    assert calls[0].extensions['timeout'] == {'connect': 5, 'read': 220, 'write': 220, 'pool': 220}
    browser.headers.pop('Authorization')
    assert browser.get(URL).status_code == 401
    assert len(calls) == 1


def test_leading_zero_number_is_preserved(client):
    browser, calls, replies = client
    number = '0001234567'
    replies.append(gateway_response(HTML.replace(NUMBER, number)))
    response = browser.get('/api/grading/cgc/certificates/' + number)
    assert response.status_code == 200
    assert response.json()['cert_number'] == number
    assert response.json()['certificate']['fields']['Cert #'] == number
    assert json.loads(calls[0].content)['url'] == f'https://www.cgccards.com/certlookup/{number}/'


@pytest.mark.parametrize('number', ['abc', '１２３'])
def test_invalid_number_never_fetches(client, number):
    browser, calls, _ = client
    assert browser.get('/api/grading/cgc/certificates/' + number).status_code == 422
    assert not calls


def test_missing_proxy_configuration_has_cgc_error(client, monkeypatch):
    browser, calls, _ = client
    monkeypatch.setenv('PROXY_GO_API_KEY', '')
    response = browser.get(URL)
    assert response.status_code == 503
    assert response.json() == {'detail': 'cgc_not_configured'}
    assert response.headers['cache-control'] == 'no-store'
    assert not calls


def test_no_images_returns_empty_list(client):
    browser, _, replies = client
    soup = BeautifulSoup(HTML, 'html.parser')
    soup.select_one('.certlookup-images').decompose()
    replies.append(gateway_response(str(soup)))
    response = browser.get(URL)
    assert response.status_code == 200
    assert response.json()['images'] == []
    assert response.json()['certificate']['grade'] == 'PRISTINE 10'


@pytest.mark.parametrize('reply,status,detail', [
    (gateway_response(target_status=403), 502, 'cgc_unavailable'),
    (httpx.Response(403, json={'detail': 'private gateway detail'}), 502, 'cgc_authentication_failed'),
    (gateway_response(target_status=429), 502, 'cgc_rate_limited'),
    (httpx.Response(504, json={'detail': 'private gateway detail'}), 504, 'cgc_timeout'),
    (httpx.ReadTimeout('private gateway detail'), 504, 'cgc_timeout'),
])
def test_target_and_gateway_error_mapping(client, reply, status, detail):
    browser, calls, replies = client
    replies.append(reply)
    response = browser.get(URL)
    assert response.status_code == status
    assert response.json() == {'detail': detail}
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1


@pytest.mark.parametrize('html', [HTML.replace(NUMBER, '9999999999'), '<html>Challenge</html>'])
def test_wrong_certificate_or_challenge_is_unreadable(client, html):
    browser, calls, replies = client
    replies.append(gateway_response(html))
    response = browser.get(URL)
    assert response.status_code == 502
    assert response.json() == {'detail': 'cgc_unreadable'}
    assert len(calls) == 1


def test_prefilled_landing_page_is_not_a_confirmed_missing_certificate(client):
    browser, _, replies = client
    html = (Path(__file__).parent / 'fixtures/cgc_lookup_pending.html').read_text(encoding='utf-8')
    replies.append(gateway_response(html))
    response = browser.get('/api/grading/cgc/certificates/0000000000')
    assert response.status_code == 502
    assert response.json() == {'detail': 'cgc_unreadable'}
