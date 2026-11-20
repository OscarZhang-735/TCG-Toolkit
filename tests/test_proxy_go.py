"""Proxy-Go gateway contract; upstream traffic stays in MockTransport."""
import base64
import json

import httpx
import pytest

from tcg_toolkit.external.proxy_go import ProxyGoClient, ProxyGoGatewayError


TARGET = 'https://www.psacard.com/en-CA/cert/98428602/psa'
TRAFFIC = {
    'upload_bytes': 120,
    'download_bytes': 880,
    'total_bytes': 1000,
    'attempts': [{'attempt_id': 'session-1', 'upload_bytes': 120,
                  'download_bytes': 880, 'total_bytes': 1000}],
}


def client_for(handler):
    return ProxyGoClient(api_url='http://gateway.test',
                         api_key='test-gateway-secret',
                         transport=httpx.MockTransport(handler))


def test_get_forwards_contract_and_preserves_target_response():
    def respond(request):
        assert request.method == 'POST'
        assert str(request.url) == 'http://gateway.test/v1/fetch'
        assert request.headers['authorization'] == 'Bearer test-gateway-secret'
        assert json.loads(request.content) == {'url': TARGET, 'country': 'US', 'method': 'GET'}
        return httpx.Response(200, json={
            'request_id': 'request-1', 'country': 'US', 'status_code': 200,
            'headers': {'content-type': 'text/html; charset=utf-8'},
            'body_base64': base64.b64encode('证书正文'.encode()).decode(), 'proxy_traffic': TRAFFIC,
        })

    with client_for(respond) as client:
        response = client.get(TARGET, country='us')
    assert response.status_code == 200
    assert response.url == TARGET
    assert response.text == '证书正文'
    assert 'authorization' not in response.request.headers
    assert response.extensions['proxy_go'] == {
        'request_id': 'request-1', 'country': 'US', 'proxy_traffic': TRAFFIC,
    }


def test_gateway_failure_exposes_detail_and_consumed_traffic():
    detail = {'code': 'challenge_retries_exhausted', 'attempts': 3,
              'last_error': 'challenge_timeout'}
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(504, json={'detail': detail}, headers={
            'X-Request-ID': 'failed-request', 'X-Proxy-Total-Bytes': '12345',
            'X-Proxy-Attempts': '3',
        })

    with client_for(respond) as client, pytest.raises(ProxyGoGatewayError) as caught:
        client.get(TARGET)
    error = caught.value
    assert error.status_code == 504
    assert error.detail == detail
    assert error.request_id == 'failed-request'
    assert error.proxy_total_bytes == 12345
    assert error.proxy_attempts == 3
    assert error.request.url == TARGET
    assert 'proxy-go' in str(error) and '504' in str(error)
    assert 'test-gateway-secret' not in str(error)
    assert 'challenge_retries_exhausted' not in str(error)
    assert len(calls) == 1
