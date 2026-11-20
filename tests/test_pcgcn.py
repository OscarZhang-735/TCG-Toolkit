import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from tcg_toolkit.grading import adapters as grading

PAYLOAD = json.loads((Path(__file__).parent / 'fixtures/pcgcn_certificate.json').read_text(encoding='utf-8'))


def test_real_certificate(monkeypatch):
    def get(self, url, *, params):
        assert url == grading.PCGCN_API
        assert params['proof_number'] == '10198854'
        return httpx.Response(200, json=PAYLOAD, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx.Client, 'get', get)
    result = grading.fetch_pcgcn('10198854')
    assert result['certificate']['title'] == 'SHIKIBE MAYU'
    assert result['certificate']['fields']['Card Number'] == 'YZ-411'
    assert result['certificate']['fields']['Surface'] == '9'
    assert result['certificate']['population']['total_grading'] == 1
    assert len(result['images']) == 2
    assert 'post_number' not in json.dumps(result)


@pytest.mark.parametrize('payload,number,status', [
    ({'code': 400, 'data': None, 'msg': '卡片不存在'}, '1', 404),
    (PAYLOAD, '0010198854', 502),
])
def test_missing_and_invalid(payload, number, status):
    with pytest.raises(HTTPException) as error:
        grading.parse_pcgcn(payload, number)
    assert error.value.status_code == status
