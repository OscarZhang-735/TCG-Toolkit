from test_product_details import client, FakeClient
from tcg_toolkit.external.search_proxy import direct_search


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
