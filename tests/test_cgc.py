from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from tcg_toolkit.grading import adapters as module

NUMBER = '6091052028'
FIXTURES = Path(__file__).parent / 'fixtures'


def test_lookup_real_certificate_with_original_images(monkeypatch):
    def extract(url, **kwargs):
        assert url == f'https://www.cgccards.com/certlookup/{NUMBER}/'
        html = (FIXTURES / 'cgc_certificate.html').read_text(encoding='utf-8')
        return httpx.Response(200, text=html, headers={'content-type': 'text/html'})

    monkeypatch.setattr(module, 'extract_http_response', extract)
    result = module.fetch_cgc(NUMBER)
    assert result['cert_number'] == NUMBER
    certificate = result['certificate']
    assert certificate['grade'] == 'PRISTINE 10'
    assert certificate['fields']['Card Number'] == 'R01-026SP'
    assert certificate['population'] == {'at_grade': None, 'higher': None}
    front, back = result['images']
    assert front['url'].endswith('/CAR6091052-028_OBV.jpg')
    assert back['url'].endswith('/CAR6091052-028_REV.jpg')


def test_prefilled_landing_page_is_not_a_confirmed_missing_certificate():
    html = (FIXTURES / 'cgc_lookup_pending.html').read_text(encoding='utf-8')
    with pytest.raises(HTTPException) as error:
        module.parse_cgc(html, '0000000000')
    assert (error.value.status_code, error.value.detail) == (502, 'cgc_unreadable')
