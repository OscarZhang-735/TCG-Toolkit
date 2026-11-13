from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi import HTTPException
from fastapi.testclient import TestClient

from auth_helpers import register_user
from tcg_toolkit import grading as ars
from tcg_toolkit.web import app

NUMBER = '047088950114'
HTML = (Path(__file__).parent / 'fixtures/ars_certificate.html').read_text(encoding='utf-8')
MISSING = (Path(__file__).parent / 'fixtures/ars_not_found.html').read_text(encoding='utf-8')
URL = '/api/grading/ars/certificates/' + NUMBER


def test_parse_real_fixture():
    result = ars.parse_ars(HTML, NUMBER)
    assert result['images'] == []
    assert result['source_url'] == ars.ARS_URL + '?id=' + NUMBER
    cert = result['certificate']
    assert cert['fields']['カード名称'] == '学生兼研究者 式部 茉優'
    assert cert['fields']['Card Name'] == 'Student and Researcher, Mayu Shikibe'
    assert cert['fields']['Card ID'] == 'LO-1606-S'
    assert cert['grade'] == '10'
    assert cert['population']['total_grading'] == 1
    assert cert['population']['total_submission'] == 2
    assert cert['population']['grades']['10'] == '1（0）'
    assert 'MG' in cert['population']['remarks']
    extra = HTML.replace('</table>', '<tr><th>Future field</th><td>kept</td></tr></table>', 1)
    assert ars.parse_ars(extra, NUMBER)['certificate']['fields']['Future field'] == 'kept'


@pytest.mark.parametrize('selector', ['main', '.card-info', '.highlight-grade', '.totals .main',
                                    '.totals .sub', '.remarks', 'input#word'])
def test_missing_structure_is_not_not_found(selector):
    soup = BeautifulSoup(HTML, 'html.parser')
    soup.select_one(selector).decompose()
    with pytest.raises(HTTPException) as exc:
        ars.parse_ars(str(soup), NUMBER)
    assert (exc.value.status_code, exc.value.detail) == (502, 'ars_unreadable')


@pytest.mark.parametrize('markup', [HTML.replace(NUMBER, '999999999999'),
    HTML.replace('class="grade-entry" data-grade="9"', 'class="grade-entry highlight-grade" data-grade="9"'),
    HTML.replace('data-grade="10"', 'data-grade=""'), '<html>Challenge</html>'])
def test_unexpected_content(markup):
    with pytest.raises(HTTPException) as exc:
        ars.parse_ars(markup, NUMBER)
    assert exc.value.detail == 'ars_unreadable'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', 'https://testserver')
    monkeypatch.setenv('TCG_PSA_API_TOKEN', 'must-not-send')
    original = httpx.Client
    calls, replies = [], []
    def handler(request):
        calls.append(request)
        assert 'authorization' not in request.headers
        assert 'cookie' not in request.headers
        reply = replies.pop(0) if replies else httpx.Response(200, text=HTML, headers={'content-type': 'text/html; charset=utf-8'})
        if isinstance(reply, Exception):
            raise reply
        return reply
    def factory(**kwargs):
        assert kwargs == {'timeout': 20, 'follow_redirects': False, 'trust_env': False}
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(ars, 'httpx', SimpleNamespace(Client=factory, TimeoutException=httpx.TimeoutException, HTTPError=httpx.HTTPError))
    with TestClient(app, base_url='https://testserver', headers={'Origin': 'https://testserver'}) as browser:
        browser.headers['Authorization'] = 'Bearer ' + register_user(browser).json()['token']
        yield browser, calls, replies


def test_authenticated_lookup(client):
    browser, calls, _ = client
    response = browser.get('/api/grading/ars/certificates/%20' + NUMBER + '%20')
    assert response.status_code == 200
    assert response.json() == ars.parse_ars(HTML, NUMBER)
    assert response.headers['cache-control'] == 'no-store'
    assert len(calls) == 1
    assert str(calls[0].url) == ars.ARS_URL + '?id=' + NUMBER
    assert set(calls[0].extensions['timeout'].values()) == {20}
    browser.headers.pop('Authorization')
    assert browser.get(URL).status_code == 401
    assert len(calls) == 1


@pytest.mark.parametrize('number', ['abc', '１２３', '1'*33, '%20'])
def test_invalid_numbers(client, number):
    browser, calls, _ = client
    assert browser.get('/api/grading/ars/certificates/' + number).status_code == 422
    assert not calls


@pytest.mark.parametrize('reply,status,code', [
    (httpx.Response(200, text=MISSING.replace('000000000000', NUMBER), headers={'content-type':'text/html'}), 404, 'grading_certificate_not_found'),
    (httpx.Response(200, text='<main>private</main>', headers={'content-type':'text/html'}), 502, 'ars_unreadable'),
    (httpx.Response(200, json={}), 502, 'ars_unreadable'),
    (httpx.Response(429, text='private'), 502, 'ars_rate_limited'),
    (httpx.Response(403, text='private'), 502, 'ars_unavailable'),
    (httpx.Response(404, text='private'), 502, 'ars_unavailable'),
    (httpx.Response(500, text='private'), 502, 'ars_unavailable'),
    (httpx.Response(302, headers={'location':'https://example.com'}), 502, 'ars_unavailable'),
    (httpx.ReadTimeout('private'), 504, 'ars_timeout'),
    (httpx.ConnectError('private'), 502, 'ars_unavailable'),
])
def test_errors(client, reply, status, code):
    browser, calls, replies = client
    replies.append(reply)
    response = browser.get(URL)
    assert response.status_code == status
    assert response.json() == {'detail': code}
    assert len(calls) == 1
