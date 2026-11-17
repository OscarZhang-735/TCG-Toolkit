"""Proxy-Go gateway contract; upstream traffic stays in MockTransport."""
import base64
import json
import os

import httpx
import pytest

from tcg_toolkit.external.proxy_go import (
    ProxyGoClient, ProxyGoConfigError, ProxyGoGatewayError,
)
from tcg_toolkit.external.search_proxy import direct_search


TARGET = 'https://www.psacard.com/en-CA/cert/98428602/psa'
TRAFFIC = {
    'upload_bytes': 120,
    'download_bytes': 880,
    'total_bytes': 1000,
    'attempts': [{'attempt_id': 'session-1', 'upload_bytes': 120,
                  'download_bytes': 880, 'total_bytes': 1000}],
}


def payload(**changes):
    result = {
        'request_id': 'request-1',
        'country': 'US',
        'status_code': 200,
        'headers': {'content-type': 'text/html; charset=utf-8'},
        'body_base64': base64.b64encode('证书正文'.encode()).decode(),
        'proxy_traffic': TRAFFIC,
    }
    result.update(changes)
    return result


def client_for(handler, **kwargs):
    return ProxyGoClient(api_url='http://gateway.test/v1/fetch',
                         api_key='test-gateway-secret',
                         transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.parametrize('endpoint', ['http://gateway.test', 'http://gateway.test/v1/fetch'])
def test_get_forwards_contract_and_preserves_target_response(endpoint):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.method == 'POST'
        assert str(request.url) == 'http://gateway.test/v1/fetch'
        assert request.headers['authorization'] == 'Bearer test-gateway-secret'
        assert request.extensions['timeout'] == {
            'connect': 5, 'read': 220, 'write': 220, 'pool': 220,
        }
        assert json.loads(request.content) == {'url': TARGET, 'country': 'US', 'method': 'GET'}
        return httpx.Response(200, json=payload())

    # Explicit proxy-go calls remain proxied even when YuYu-Tei is in direct mode.
    token = direct_search.set(True)
    try:
        with ProxyGoClient(api_url=endpoint, api_key='test-gateway-secret',
                           transport=httpx.MockTransport(respond)) as client:
            response = client.get(TARGET, country='us')
            assert not client.is_closed
            assert response.status_code == 200
            assert response.text == '证书正文'
            assert response.url == TARGET
            assert response.request.method == 'GET'
            assert 'authorization' not in response.request.headers
            assert response.extensions['proxy_go'] == {
                'request_id': 'request-1', 'country': 'US', 'proxy_traffic': TRAFFIC,
            }
            response.raise_for_status()
        assert client.is_closed
    finally:
        direct_search.reset(token)
    assert len(calls) == 1


def test_binary_body_and_nullable_traffic():
    body = b'\x00\xff\x80image\r\n'
    with client_for(lambda request: httpx.Response(200, json=payload(
            body_base64=base64.b64encode(body).decode(),
            headers={'content-type': 'application/octet-stream'}, proxy_traffic=None))) as client:
        response = client.get(TARGET)
    assert response.content == body
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.extensions['proxy_go']['proxy_traffic'] is None


@pytest.mark.parametrize('method', ['head', 'fetch'])
def test_head_and_fetch_forward_supported_method(method):
    def respond(request):
        assert json.loads(request.content) == {'url': TARGET, 'country': 'JP', 'method': 'HEAD'}
        return httpx.Response(200, json=payload(country='JP', body_base64=''))

    with client_for(respond) as client:
        response = (client.head(TARGET, country='JP') if method == 'head'
                    else client.fetch(TARGET, country='jp', method='head'))
    assert response.content == b''
    assert response.request.method == 'HEAD'


@pytest.mark.parametrize('status', [403, 429, 302])
def test_target_failure_or_redirect_is_returned_without_retry(status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=payload(status_code=status, headers={
            'location': 'https://target.example/next', 'retry-after': '30',
        }))

    with client_for(respond) as client:
        response = client.get(TARGET)
    assert response.status_code == status
    assert response.headers['location'] == 'https://target.example/next'
    assert response.headers['retry-after'] == '30'
    with pytest.raises(httpx.HTTPStatusError) as caught:
        response.raise_for_status()
    assert caught.value.request.url == TARGET
    assert len(calls) == 1


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


@pytest.mark.parametrize('status,detail', [(401, 'unauthorized'), (422, [{'msg': 'invalid URL'}])])
def test_early_gateway_errors_need_no_diagnostic_headers(status, detail):
    with client_for(lambda request: httpx.Response(status, json={'detail': detail})) as client:
        with pytest.raises(ProxyGoGatewayError) as caught:
            client.get(TARGET)
    assert caught.value.detail == detail
    assert caught.value.request_id is None
    assert caught.value.proxy_total_bytes is None
    assert caught.value.proxy_attempts is None


@pytest.mark.parametrize('status', [302, 502])
def test_non_json_gateway_error_is_not_followed(status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, text='upstream error', headers={'location': 'https://other.test'})

    with client_for(respond) as client, pytest.raises(ProxyGoGatewayError) as caught:
        client.get(TARGET)
    assert caught.value.status_code == status
    assert caught.value.detail is None
    assert len(calls) == 1


@pytest.mark.parametrize('reply', [
    None, {}, payload(body_base64='!!!'), payload(status_code=True),
])
def test_invalid_success_envelope_is_a_transport_error(reply):
    with client_for(lambda request: httpx.Response(200, json=reply)) as client:
        with pytest.raises(httpx.TransportError):
            client.get(TARGET)


def test_non_json_success_is_a_transport_error():
    with client_for(lambda request: httpx.Response(200, text='<html>not JSON</html>')) as client:
        with pytest.raises(httpx.TransportError):
            client.get(TARGET)


def test_network_timeout_propagates_without_retry():
    calls = []

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout('gateway timed out', request=request)

    with client_for(respond) as client, pytest.raises(httpx.ReadTimeout):
        client.get(TARGET)
    assert len(calls) == 1


@pytest.mark.parametrize('kwargs', [{'method': 'POST'}, {'country': 'USA'}, {'country': '中国'}])
def test_unsupported_requests_fail_before_network(kwargs):
    with client_for(lambda request: pytest.fail('Invalid input reached the gateway')) as client:
        with pytest.raises(ValueError):
            client.fetch(TARGET, **kwargs)


def test_configuration_precedence_and_dotenv_does_not_mutate_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('PROXY_GO_API', raising=False)
    monkeypatch.delenv('PROXY_GO_API_KEY', raising=False)
    (tmp_path / '.env').write_text(
        'PROXY_GO_API=http://file.test/v1/fetch\nPROXY_GO_API_KEY=file-secret\n', encoding='utf-8')
    calls = []

    def respond(request):
        calls.append((str(request.url), request.headers['authorization']))
        return httpx.Response(200, json=payload())

    with ProxyGoClient(transport=httpx.MockTransport(respond)) as client:
        client.get(TARGET)
    assert 'PROXY_GO_API' not in os.environ and 'PROXY_GO_API_KEY' not in os.environ
    monkeypatch.setenv('PROXY_GO_API', 'http://env.test')
    monkeypatch.setenv('PROXY_GO_API_KEY', 'env-secret')
    with ProxyGoClient(transport=httpx.MockTransport(respond)) as client:
        client.get(TARGET)
    with ProxyGoClient(api_url='http://argument.test', api_key='argument-secret',
                       transport=httpx.MockTransport(respond)) as client:
        client.get(TARGET)
    assert calls == [
        ('http://file.test/v1/fetch', 'Bearer file-secret'),
        ('http://env.test/v1/fetch', 'Bearer env-secret'),
        ('http://argument.test/v1/fetch', 'Bearer argument-secret'),
    ]


@pytest.mark.parametrize('variable', ['PROXY_GO_API', 'PROXY_GO_API_KEY'])
def test_empty_environment_setting_overrides_dotenv(monkeypatch, tmp_path, variable):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('PROXY_GO_API', raising=False)
    monkeypatch.delenv('PROXY_GO_API_KEY', raising=False)
    (tmp_path / '.env').write_text(
        'PROXY_GO_API=http://file.test\nPROXY_GO_API_KEY=file-secret\n', encoding='utf-8')
    monkeypatch.setenv(variable, '')
    with pytest.raises(ProxyGoConfigError):
        with ProxyGoClient(transport=httpx.MockTransport(
                lambda request: pytest.fail('Empty configuration reached the gateway'))) as client:
            client.get(TARGET)


@pytest.mark.parametrize('api_url,api_key', [
    ('', 'test-secret'), ('http://gateway.test', ''),
    ('http://gateway.test/wrong', 'test-secret'), ('ftp://gateway.test', 'test-secret'),
])
def test_invalid_explicit_configuration_never_sends(api_url, api_key):
    with pytest.raises(ProxyGoConfigError):
        with ProxyGoClient(api_url=api_url, api_key=api_key, transport=httpx.MockTransport(
                lambda request: pytest.fail('Invalid configuration reached the gateway'))) as client:
            client.get(TARGET)


def test_close_releases_transport():
    closed = []

    class Transport(httpx.MockTransport):
        def close(self):
            closed.append(True)

    with ProxyGoClient(api_url='http://gateway.test', api_key='test-secret',
                       transport=Transport(lambda request: httpx.Response(200, json=payload()))) as client:
        client.get(TARGET)
    assert client.is_closed
    assert closed == [True]
