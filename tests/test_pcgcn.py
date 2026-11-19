import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from tcg_toolkit.grading import adapters as grading

PAYLOAD = json.loads((Path(__file__).parent / 'fixtures/pcgcn_certificate.json').read_text(encoding='utf-8'))


def test_real_certificate():
    result = grading.parse_pcgcn(PAYLOAD, '10198854')
    assert result['certificate']['title'] == 'SHIKIBE MAYU'
    assert result['certificate']['fields']['Card Number'] == 'YZ-411'
    assert result['certificate']['fields']['Surface'] == '9'
    assert result['certificate']['population']['total_grading'] == 1
    assert len(result['images']) == 2
    assert 'post_number' not in json.dumps(result)


@pytest.mark.parametrize('payload,number,status', [
    ({'code': 400, 'data': None, 'msg': '卡片不存在'}, '1', 404),
    (PAYLOAD, '0010198854', 502), ([], '1', 502),
    ({'code': 2000, 'data': {}}, '1', 502),
    ({'code': 400, 'data': None, 'msg': 'other failure'}, '1', 502),
])
def test_missing_and_invalid(payload, number, status):
    with pytest.raises(HTTPException) as error:
        grading.parse_pcgcn(payload, number)
    assert error.value.status_code == status


@pytest.mark.parametrize('mode,status,detail', [
    ('success', 200, None), ('missing', 404, 'grading_certificate_not_found'),
    ('timeout', 504, 'pcgcn_timeout'), ('network', 502, 'pcgcn_unavailable'),
    ('429', 502, 'pcgcn_rate_limited'), ('500', 502, 'pcgcn_unavailable'),
    ('json', 502, 'pcgcn_unreadable'),
])
def test_direct_transport(monkeypatch, mode, status, detail):
    real_client = httpx.Client

    def handle(request):
        assert str(request.url).startswith(grading.PCGCN_API)
        assert request.url.params['proof_number'] == '10198854'
        assert request.url.params['pageSize'] == '1'
        assert 'authorization' not in request.headers
        if mode == 'timeout':
            raise httpx.ReadTimeout('timeout')
        if mode == 'network':
            raise httpx.ConnectError('offline')
        if mode.isdigit():
            return httpx.Response(int(mode))
        if mode == 'json':
            return httpx.Response(200, text='<html>error</html>')
        return httpx.Response(200, json=PAYLOAD if mode == 'success' else {'code': 400, 'data': None, 'msg': '卡片不存在'})

    def client(**kwargs):
        assert kwargs == {'timeout': 20, 'follow_redirects': False, 'trust_env': False}
        return real_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(grading.httpx, 'Client', client)
    if detail:
        with pytest.raises(HTTPException) as error:
            grading.fetch_pcgcn('10198854')
        assert (error.value.status_code, error.value.detail) == (status, detail)
    else:
        assert grading.fetch_pcgcn('10198854')['provider'] == 'pcgcn'
