import httpx
import pytest

@pytest.mark.parametrize('endpoint', ['http://relay.example', 'http://relay.example/encrypted-proxy'])
@pytest.mark.parametrize('failure', ['', 'tamper', 'wrong_id', 'http'])
def test_encrypted_proxy_roundtrip(monkeypatch, tmp_path, failure, endpoint):
    import base64
    from tcg_toolkit.external.proxy_wire import Wire
    from tcg_toolkit.external.search_proxy import YuYuTeiProxyTransport
    key = tmp_path / 'master.key'
    key.write_bytes(bytes(range(32)))
    monkeypatch.setenv('TCG_PROXY_KEY_FILE', str(key))
    monkeypatch.setenv('TCG_SEARCH_PROXY_URL', endpoint)
    wire = Wire(key)
    calls = []
    def respond(request):
        calls.append(request)
        assert str(request.url) == 'http://relay.example/encrypted-proxy'
        assert 'cookie' not in request.headers and 'x-csrf-token' not in request.headers
        payload = wire.open('request', request.content)
        assert payload['method'] == 'POST'
        assert payload['url'] == 'https://yuyu-tei.jp/cart'
        assert base64.b64decode(payload['body']) == b'counter=1'
        headers = dict(payload['headers'])
        assert headers['cookie'] == 'laravel_session=secret'
        assert headers['x-csrf-token'] == 'csrf-secret'
        assert headers['Accept-Encoding'] == 'identity'
        if failure == 'http':
            return httpx.Response(502)
        result = {'request_id': 'wrong' if failure == 'wrong_id' else payload['request_id'],
                  'status': 200, 'headers': [['set-cookie', 'a=1'], ['set-cookie', 'b=2']],
                  'body': base64.b64encode('中文'.encode()).decode()}
        packet = wire.seal('response', result)
        if failure == 'tamper': packet = packet[:-1] + bytes([packet[-1] ^ 1])
        return httpx.Response(200, stream=httpx.ByteStream(packet))
    with httpx.Client(transport=YuYuTeiProxyTransport(transport=httpx.MockTransport(respond))) as client:
        def send():
            return client.post('https://yuyu-tei.jp/cart', content=b'counter=1',
                               headers={'cookie': 'laravel_session=secret', 'x-csrf-token': 'csrf-secret'})
        if failure:
            with pytest.raises(httpx.TransportError): send()
        else:
            response = send()
            assert response.text == '中文'
            assert response.headers.get_list('set-cookie') == ['a=1', 'b=2']
    assert len(calls) == 1


@pytest.mark.parametrize('endpoint,key_configured', [
    ('http://relay.example/encrypted-proxy', False),
    ('http://relay.example/proxy', True),
])
def test_invalid_proxy_config_never_sends_plaintext(monkeypatch, tmp_path, endpoint, key_configured):
    from tcg_toolkit.external.search_proxy import YuYuTeiProxyTransport
    key = tmp_path / 'key'
    key.write_bytes(bytes(range(32)))
    monkeypatch.setenv('TCG_SEARCH_PROXY_URL', endpoint)
    monkeypatch.setenv('TCG_PROXY_KEY_FILE', str(key) if key_configured else '')
    calls = []
    with httpx.Client(transport=YuYuTeiProxyTransport(transport=httpx.MockTransport(
            lambda r: calls.append(r)))) as client:
        with pytest.raises(httpx.TransportError):
            client.get('https://yuyu-tei.jp/')
    assert calls == []


@pytest.mark.parametrize('switch,endpoint', [(True, 'http://relay.example/encrypted-proxy'), (False, '')])
def test_direct_mode_does_not_require_key(monkeypatch, switch, endpoint):
    from tcg_toolkit.external.search_proxy import YuYuTeiProxyTransport, direct_search
    monkeypatch.setenv('TCG_SEARCH_PROXY_URL', endpoint)
    monkeypatch.delenv('TCG_PROXY_KEY_FILE', raising=False)
    token = direct_search.set(switch)
    calls = []
    try:
        with httpx.Client(transport=YuYuTeiProxyTransport(transport=httpx.MockTransport(
                lambda r: (calls.append(r), httpx.Response(200, text='ok'))[1]))) as client:
            assert client.get('https://yuyu-tei.jp/').text == 'ok'
        assert str(calls[0].url) == 'https://yuyu-tei.jp/'
    finally:
        direct_search.reset(token)
