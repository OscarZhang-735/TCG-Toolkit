import httpx
import pytest

@pytest.mark.parametrize('tampered', [False, True])
def test_encrypted_proxy_roundtrip(monkeypatch, tmp_path, tampered):
    import base64
    from tcg_toolkit.external.proxy_wire import Wire
    from tcg_toolkit.external.search_proxy import YuYuTeiProxyTransport
    key = tmp_path / 'master.key'
    key.write_bytes(bytes(range(32)))
    monkeypatch.setenv('TCG_PROXY_KEY_FILE', str(key))
    monkeypatch.setenv('TCG_SEARCH_PROXY_URL', 'http://relay.example')
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
        result = {'request_id': payload['request_id'],
                  'status': 200, 'headers': [['set-cookie', 'a=1'], ['set-cookie', 'b=2']],
                  'body': base64.b64encode('中文'.encode()).decode()}
        packet = wire.seal('response', result)
        if tampered:
            packet = packet[:-1] + bytes([packet[-1] ^ 1])
        return httpx.Response(200, stream=httpx.ByteStream(packet))
    with httpx.Client(transport=YuYuTeiProxyTransport(transport=httpx.MockTransport(respond))) as client:
        def send():
            return client.post('https://yuyu-tei.jp/cart', content=b'counter=1',
                               headers={'cookie': 'laravel_session=secret', 'x-csrf-token': 'csrf-secret'})
        if tampered:
            with pytest.raises(httpx.TransportError): send()
        else:
            response = send()
            assert response.text == '中文'
            assert response.headers.get_list('set-cookie') == ['a=1', 'b=2']
    assert len(calls) == 1


def test_missing_key_never_sends_plaintext(monkeypatch):
    from tcg_toolkit.external.search_proxy import YuYuTeiProxyTransport
    monkeypatch.setenv('TCG_SEARCH_PROXY_URL', 'http://relay.example/encrypted-proxy')
    monkeypatch.setenv('TCG_PROXY_KEY_FILE', '')
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
