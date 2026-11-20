from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from tcg_toolkit.grading import adapters as ars

NUMBER = '047088950114'
FIXTURES = Path(__file__).parent / 'fixtures'


def test_lookup_real_certificate(monkeypatch):
    def get(self, url, *, params):
        assert url == ars.ARS_URL and params == {'id': NUMBER}
        html = (FIXTURES / 'ars_certificate.html').read_text(encoding='utf-8')
        return httpx.Response(200, text=html, headers={'content-type': 'text/html'}, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx.Client, 'get', get)
    result = ars.fetch_ars(NUMBER)
    assert result['cert_number'] == NUMBER
    cert = result['certificate']
    assert cert['title'] == 'Student and Researcher, Mayu Shikibe'
    assert cert['fields']['カード名称'] == '学生兼研究者 式部 茉優'
    assert cert['fields']['Card ID'] == 'LO-1606-S'
    assert cert['grade'] == '10'
    assert cert['population']['grades']['10'] == '1（0）'


def test_confirmed_missing_certificate():
    html = (FIXTURES / 'ars_not_found.html').read_text(encoding='utf-8')
    with pytest.raises(HTTPException) as error:
        ars.parse_ars(html, '000000000000')
    assert (error.value.status_code, error.value.detail) == (404, 'grading_certificate_not_found')
