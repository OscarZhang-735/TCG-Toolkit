from base64 import b64encode

import httpx
from test_product_details import client, FakeClient
from tcg_toolkit.external.proxy_wire import Wire
from tcg_toolkit.external.search_proxy import DEFAULT_SEARCH_PROXY_URL, SearchProxyClient, direct_search


def test_online_hidden_and_direct_rejected(client, monkeypatch):
    monkeypatch.delenv('TCG_ALLOW_PROXY_SWITCH', raising=False)
    assert client.get('/api/yuyutei/proxy-config').json() == {'available': False}
    assert client.get('/api/health', headers={'X-TCG-Direct': '1'}).status_code == 403


def test_local_mode_is_request_scoped(client, monkeypatch):
    monkeypatch.setenv('TCG_ALLOW_PROXY_SWITCH', 'true')
    assert client.get('/api/yuyutei/proxy-config').json() == {'available': True}
    modes = []
    original = FakeClient.get
    def get(self, url, *, timeout):
        modes.append(direct_search.get())
        return original(self, url, timeout=timeout)
    monkeypatch.setattr(FakeClient, 'get', get)
    params = {'gid': 8, 'ver': 'yuzu5.0', 'cid': '1', 'stock': 'true'}
    assert client.get('/api/yuyutei/product', params=params, headers={'X-TCG-Direct':'1'}).status_code == 200
    assert client.get('/api/yuyutei/product', params=params).status_code == 200
    assert modes == [True, False]
    assert direct_search.get() is False


def test_proxy_client_direct_mode_and_default(monkeypatch, tmp_path):
    monkeypatch.delenv('TCG_SEARCH_PROXY_URL', raising=False)
    key = tmp_path / 'master.key'
    key.write_bytes(bytes(range(32)))
    monkeypatch.setenv('TCG_PROXY_KEY_FILE', str(key))
    wire = Wire(key)
    target = 'https://yuyu-tei.jp/sell/ws'
    calls = []

    def respond(request):
        calls.append((request.method, str(request.url)))
        if request.url.host == 'yuyu-tei.jp':
            return httpx.Response(200, text='direct')
        payload = wire.open('request', request.content)
        assert payload['url'] == target and payload['method'] == 'GET'
        response = wire.seal('response', {'request_id': payload['request_id'],
                                         'status': 200, 'headers': [], 'body': b64encode(b'proxy').decode()})
        return httpx.Response(200, stream=httpx.ByteStream(response))

    for direct, expected in [(True, 'direct'), (False, 'proxy')]:
        token = direct_search.set(direct)
        proxy = SearchProxyClient(transport=httpx.MockTransport(respond))
        try:
            assert proxy.get(target, timeout=1).text == expected
        finally:
            proxy.close()
            direct_search.reset(token)
    assert calls == [('GET', target), ('POST', DEFAULT_SEARCH_PROXY_URL)]
