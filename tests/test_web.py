import httpx
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit import Game, SearchPage, SearchParseError, SearchRequestError
from tcg_toolkit.search import SEARCHERS
from tcg_toolkit.web import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def authenticated_business_api():
    from tcg_toolkit.auth.dependencies import current_user
    app.dependency_overrides[current_user] = lambda: {"id": "test-user"}
    yield
    app.dependency_overrides.pop(current_user, None)


def test_static_and_health():
    assert client.get('/api/health').json() == {'status': 'ok'}
    assert 'data-i18n="tcg_toolkit_search"' in client.get('/').text
    assert 'TCG Toolkit' in client.get('/locales.js').text
    assert client.get('/i18n.js').status_code == 200
    assert client.get('/app.js').status_code == 200
    assert client.get('/theme.css').status_code == 200
    assert client.get('/style.css').status_code == 200


@pytest.mark.parametrize('game', list(Game))
def test_search(game, monkeypatch):
    class Fake:
        def __init__(self, *, stock): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def search_page(self, keyword, *, page):
            assert keyword == 'OS01' and page == 2
            return SearchPage([], keyword, page, 3)
    monkeypatch.setitem(SEARCHERS, game, Fake)
    result = client.get('/api/search', params={'game': game.value, 'keyword': 'OS01', 'page': 2})
    assert result.status_code == 200
    assert result.json() == dict(source='yuyutei', items=[], keyword='OS01', page=2, next_page=3,
                                 has_next=True, game=game.value)


@pytest.mark.parametrize('params', [dict(game='bad', keyword='x'), dict(game='wsr', keyword=' '),
                                    dict(game='pm', keyword='x', source='hobbystation'),
                                    dict(game='wsr', keyword='x', source='cardmax'),
                                    dict(game='lycee', keyword='x', source='unknown'),
                                    dict(game='wsr', keyword='x', page=0), dict(game='wsr')])
def test_validation(params):
    assert client.get('/api/search', params=params).status_code == 422


@pytest.mark.parametrize(('game', 'category'), [('ws', 'ct1847'), ('sev', 'ct1740')])
def test_cardmax_search(game, category, monkeypatch):
    from pathlib import Path
    from tcg_toolkit.cardmax_search import CardMaxSearcher
    def respond(request):
        assert dict(request.url.params) == {'search': 'test', 'category': category}
        html = (Path(__file__).parent / 'fixtures' / f'cardmax_{game}.html').read_text(encoding='utf-8')
        return httpx.Response(200, text=html)
    monkeypatch.setattr(CardMaxSearcher, 'client_type', staticmethod(
        lambda: httpx.Client(transport=httpx.MockTransport(respond))))
    response = client.get('/api/search', params={'source': 'cardmax', 'game': game, 'keyword': 'test'})
    assert response.status_code == 200
    data = response.json()
    assert data['source'] == 'cardmax'
    assert data['game'] == game and data['items'][0]['game'] == game
    assert data['has_next'] == (game == 'sev')


@pytest.mark.parametrize(('game', 'path'), [('lycee', 'lo'), ('ws', 'ws'), ('wsr', 'wr')])
def test_hobbystation_search(game, path, monkeypatch):
    def get(self, url, *, timeout):
        assert url == f'https://www.hobbystation-single.jp/{path}/product/list?search_word=abc&match_type=1'
        html = '<main><div class="ec-searchnavRole__counter">お探しの商品は見つかりませんでした</div></main>'
        return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr('tcg_toolkit.external.search_proxy.SearchProxyClient.get', get)
    result = client.get('/api/search', params={'source': 'hobbystation', 'game': game, 'keyword': 'abc', 'stock': True})
    assert result.status_code == 200
    assert result.json() == dict(source='hobbystation', game=game, keyword='abc', page=1,
                                 items=[], next_page=None, has_next=False)


def test_hobbystation_result_image_loads(monkeypatch):
    from pathlib import Path
    from contextlib import contextmanager
    html = (Path(__file__).parent / 'fixtures/hobbystation_lo.html').read_text(encoding='utf-8')
    def get(self, url, *, timeout):
        return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr('tcg_toolkit.external.search_proxy.SearchProxyClient.get', get)
    result = client.get('/api/search', params={'source': 'hobbystation', 'game': 'lycee', 'keyword': 'LO-4899'})
    image_url = result.json()['items'][0]['image_url']
    assert image_url == 'http://testserver/api/images/hobbystation/lo/LO-LO-4899.jpg'
    @contextmanager
    def stream(self, method, url, *, headers):
        assert url == 'https://www.hobbystation-single.jp/html/upload/save_image/lo/LO-LO-4899.jpg'
        assert headers['Referer'] == 'https://www.hobbystation-single.jp/'
        yield httpx.Response(200, headers={'content-type': 'image/jpeg'}, content=b'jpeg',
                             request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.Client, 'stream', stream)
    image = client.get(image_url)
    assert image.status_code == 200 and image.content == b'jpeg'
    assert image.headers['content-type'] == 'image/jpeg'
    assert client.get('/api/images/hobbystation/lo/other.svg').status_code == 404


@pytest.mark.parametrize('kind,status', [('request', 502), ('timeout', 504), ('parse', 502)])
def test_errors(kind, status, monkeypatch):
    class Broken:
        def __init__(self, *, stock): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def search_page(self, *args, **kwargs):
            error = SearchParseError if kind == 'parse' else SearchRequestError
            cause = httpx.ReadTimeout('timeout') if kind == 'timeout' else None
            raise error('internal detail', game=Game.WSR, keyword='x', page=1, url='url') from cause
    monkeypatch.setitem(SEARCHERS, Game.WSR, Broken)
    response = client.get('/api/search', params={'game': 'wsr', 'keyword': 'x'})
    assert response.status_code == status
    assert 'message' in response.json()['detail']
    assert 'internal detail' not in response.text


@pytest.mark.parametrize("game", ["ws", "pm", "sev"])
def test_new_game_controls(game):
    from bs4 import BeautifulSoup
    page = BeautifulSoup(client.get('/').text, 'html.parser')
    assert page.select_one(f'input[name="source"][value="{game}"][checked]')
    template = page.find('template', id='filter-panel')
    assert template is not None
    game_filter = template.find('input', attrs={'data-role': 'game', 'value': game})
    assert game_filter is not None and game_filter.has_attr('checked')


def test_sev_search_and_logo(monkeypatch):
    from pathlib import Path
    from tcg_toolkit import SEVSearcher
    assert SEARCHERS[Game.SEV] is SEVSearcher
    html = (Path(__file__).parent / 'fixtures/sev_1.html').read_text(encoding='utf-8')
    def get(self, url, *, timeout):
        assert url == 'https://yuyu-tei.jp/sell/sev/s/search?search_word=BP01'
        return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr('tcg_toolkit.external.search_proxy.SearchProxyClient.get', get)
    response = client.get('/api/search', params={'game': 'sev', 'keyword': 'BP01'})
    assert response.status_code == 200
    item = response.json()['items'][0]
    assert item['game'] == 'sev' and item['card_number'] == 'BP01-U01'
    assert item['cart']['gid'] == 47
    logo = client.get('/game-sev.png')
    assert logo.status_code == 200 and logo.headers['content-type'] == 'image/png'
