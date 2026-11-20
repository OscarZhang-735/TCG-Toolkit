from test_product_details import client, FakeClient
from tcg_toolkit.external.search_proxy import direct_search
from tcg_toolkit.catalog.pagination import PagedSearcher
from tcg_toolkit.catalog.models import SearchPage


def test_production_direct_route_only_for_hobby_search(client, monkeypatch):
    observed = []
    def page(searcher, keyword, *, page):
        observed.append(direct_search.get())
        return SearchPage([], keyword, page, None, searcher.source)
    monkeypatch.setattr(PagedSearcher, 'search_page', page)
    params = {'game': 'ws', 'keyword': '立希'}
    for setting, source in [('true', 'hobbystation'), ('true', 'yuyutei'), ('false', 'hobbystation')]:
        monkeypatch.setenv('TCG_HOBBYSTATION_WEB_SEARCH_DIRECT', setting)
        assert client.get('/api/search', params={**params, 'source': source}).status_code == 200
    assert observed == [True, False, False]


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
