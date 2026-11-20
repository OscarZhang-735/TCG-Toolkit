from pathlib import Path
import httpx
import pytest
from tcg_toolkit.catalog.providers.official import parse_lycee
from test_cards import client

def test_lo1606():
    items = parse_lycee(Path('tests/fixtures/official_lycee.html').read_text(encoding='utf-8'))
    assert [(x['card_number'], x['rarity']) for x in items] == [('LO-1606-S', 'SP'), ('LO-1606', 'SR')]
    assert all(x['name'] and x['game'] == 'Lycee' for x in items)
    assert [x['image_url'] for x in items] == ['https://lycee-tcg.com/card/image/LO-1606-S.png', 'https://lycee-tcg.com/card/image/LO-1606.png']

def test_empty_and_invalid():
    assert parse_lycee('<input name="word">') == []
    with pytest.raises(ValueError):
        parse_lycee('<html>Unavailable</html>')

def test_api(client, monkeypatch):
    def get(self, url, **kwargs):
        assert url == 'https://lycee-tcg.com/card/'
        assert kwargs['params']['word'] == 'LO-1606'
        return httpx.Response(200, text=Path('tests/fixtures/official_lycee.html').read_text(encoding='utf-8'), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    # Use request rather than get: TestClient inherits httpx.Client.
    response = client.request('GET', '/api/official/lycee/search?word=LO-1606')
    assert response.status_code == 200 and len(response.json()['items']) == 2
    client.headers.pop('Authorization')
    assert client.request('GET', '/api/official/lycee/search?word=LO-1606').status_code == 401


def test_ws_versions():
    import json
    from tcg_toolkit.catalog.providers.official import parse_ws
    data = json.loads(Path('tests/fixtures/official_ws.json').read_text(encoding='utf-8'))
    items = parse_ws(data)
    assert [x['card_number'] for x in items] == ['BD/WE49-56', 'BD/WE49-56BDR', 'BD/WE49-56SP']
    assert [x['rarity'] for x in items] == ['N', 'BDR', 'SP']
    assert all(x['game'] == 'WS' and x['image_url'].startswith('https://ws-tcg.com/') for x in items)
    assert parse_ws({'items': []}) == []
    for invalid in [{}, {'items': [{}]}, {'items': None}]:
        with pytest.raises(ValueError):
            parse_ws(invalid)


def test_ws_api(client, monkeypatch):
    from tcg_toolkit.catalog.providers.official import WS_SEARCH
    def get(self, url, **kwargs):
        assert url == WS_SEARCH and kwargs['params'] == {'keyword': 'WE49-56'}
        return httpx.Response(200, text=Path('tests/fixtures/official_ws.json').read_text(encoding='utf-8'), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    response = client.request('GET', '/api/official/ws/search?word=WE49-56')
    assert response.status_code == 200 and response.json()['total'] == 3
    assert client.request('GET', '/api/official/ws/search?word=%20').status_code == 422
    client.headers.pop('Authorization')
    assert client.request('GET', '/api/official/ws/search?word=WE49-56').status_code == 401


@pytest.mark.parametrize('failure,status', [('timeout', 504), ('invalid', 502)])
def test_ws_failure(client, monkeypatch, failure, status):
    def get(self, url, **kwargs):
        if failure == 'timeout':
            raise httpx.ReadTimeout('timeout')
        return httpx.Response(200, text='{}', request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    assert client.request('GET', '/api/official/ws/search?word=WE49-56').status_code == status


def test_wsr_parsing():
    from tcg_toolkit.catalog.providers.official import parse_wsr, wsrose_rarity
    items, total = parse_wsr(Path('tests/fixtures/official_wsr.html').read_text(encoding='utf-8'))
    assert total == 3 and len(items) == 3
    assert items[0]['card_number'] == 'OS13/R13-001' and items[0]['game'] == 'WSR'
    detail = Path('tests/fixtures/official_wsr_detail.html').read_text(encoding='utf-8')
    assert wsrose_rarity(detail, 'OS13/R13-001') == 'RR'
    with pytest.raises(ValueError):
        wsrose_rarity(detail, 'wrong')
    assert parse_wsr('<div class="search-Hits"><span>0</span></div>') == ([], 0)
    with pytest.raises(ValueError):
        parse_wsr('<html>error</html>')


def test_wsr_api(client, monkeypatch):
    from tcg_toolkit.catalog.providers.official import WSR_SEARCH, WSR_DETAIL
    detail = Path('tests/fixtures/official_wsr_detail.html').read_text(encoding='utf-8')
    calls = []
    def get(self, url, **kwargs):
        calls.append(url)
        if url == WSR_SEARCH:
            assert kwargs['params'] == {'keyword': 'OS13/R13-001'}
            text = Path('tests/fixtures/official_wsr.html').read_text(encoding='utf-8')
        else:
            assert url == WSR_DETAIL
            number = kwargs['params']['cardno']
            rarity = 'OFR' if number.endswith('OFR') else 'SEC' if number.endswith('SEC') else 'RR'
            text = detail.replace('OS13/R13-001', number).replace('RR', rarity)
        return httpx.Response(200, text=text, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    response = client.request('GET', '/api/official/wsr/search?word=OS13/R13-001')
    assert response.status_code == 200
    assert [r['rarity'] for r in response.json()['items']] == ['RR', 'OFR', 'SEC']
    assert len(calls) == 4
    client.headers.pop('Authorization')
    assert client.request('GET', '/api/official/wsr/search?word=test').status_code == 401


@pytest.mark.parametrize('failure,status', [('timeout', 504), ('invalid', 502)])
def test_wsr_failure(client, monkeypatch, failure, status):
    def get(self, url, **kwargs):
        if failure == 'timeout':
            raise httpx.ReadTimeout('timeout')
        return httpx.Response(200, text='Unavailable', request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    assert client.request('GET', '/api/official/wsr/search?word=test').status_code == status


def test_pm_metadata_and_duplicate_numbers():
    from tcg_toolkit.catalog.providers.official import parse_pm
    items = parse_pm(Path('tests/fixtures/official_pm.html').read_text(encoding='utf-8'))
    assert len(items) == 6
    assert all(x['game'] == 'PM' and x['card_number'] == '01-001' for x in items)
    assert items[0]['rarity'] == 'UC' and items[1]['rarity'] == 'R'
    assert items[0]['name'] != items[1]['name']
    assert items[0]['image_url'].endswith('PM_KG_01-001.jpg')
    assert parse_pm('<input name="keyword_card">') == []
    with pytest.raises(ValueError):
        parse_pm('<html>Unavailable</html>')


def test_pm_api(client, monkeypatch):
    from tcg_toolkit.catalog.providers.official import PM_SEARCH
    def get(self, url, **kwargs):
        assert url == PM_SEARCH
        assert kwargs['params'] == {'s_flg': 'on', 'keyword_card': '01-001'}
        return httpx.Response(200, text=Path('tests/fixtures/official_pm.html').read_text(encoding='utf-8'), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    assert len(client.request('GET', '/api/official/pm/search?word=01-001').json()['items']) == 6
    assert client.request('GET', '/api/official/pm/search?word=%20').status_code == 422
    client.headers.pop('Authorization')
    assert client.request('GET', '/api/official/pm/search?word=01-001').status_code == 401


@pytest.mark.parametrize('failure,status', [('timeout', 504), ('invalid', 502)])
def test_pm_failure(client, monkeypatch, failure, status):
    def get(self, url, **kwargs):
        if failure == 'timeout':
            raise httpx.ReadTimeout('timeout')
        return httpx.Response(200, text='Unavailable', request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    assert client.request('GET', '/api/official/pm/search?word=test').status_code == status


def test_sev_en_exact_detail():
    from tcg_toolkit.catalog.providers.official import parse_sev_en
    html = Path('tests/fixtures/official_sev_en.html').read_text(encoding='utf-8')
    item = parse_sev_en(html, 'CP04-021EN')[0]
    assert (item['game'], item['name'], item['rarity']) == ('SEV EN', 'Christina', 'L')
    assert item['image_url'].endswith('/CP04/CP04-021EN.png')
    for number in ['CP04-021', 'CP04-022EN', 'cp04-021en']:
        assert parse_sev_en(html, number) == []
    assert parse_sev_en('<form id="searchForm"></form>', 'CP04-021') == []
    with pytest.raises(ValueError):
        parse_sev_en('<html>error</html>', 'CP04-021EN')


def test_sev_en_api(client, monkeypatch):
    def get(self, url, **kwargs):
        assert url == 'https://en.shadowverse-evolve.com/cards/'
        assert kwargs['params'] == {'cardno': 'CP04-021EN'}
        return httpx.Response(200, text=Path('tests/fixtures/official_sev_en.html').read_text(encoding='utf-8'), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    response = client.request('GET', '/api/official/sev_en/search?word=CP04-021EN')
    assert response.status_code == 200
    assert response.json()['items'][0]['card_number'] == 'CP04-021EN'
    client.headers.pop('Authorization')
    assert client.request('GET', '/api/official/sev_en/search?word=CP04-021EN').status_code == 401


def test_sev_japanese_exact_detail():
    from tcg_toolkit.catalog.providers.official import parse_sev
    html = Path('tests/fixtures/official_sev.html').read_text(encoding='utf-8')
    item = parse_sev(html, 'CP04-021')[0]
    assert (item['game'], item['name'], item['rarity']) == ('SEV', 'クリスティーナ', 'LG')
    assert item['image_url'] == 'https://shadowverse-evolve.com/wordpress/wp-content/images/cardlist/CP04/cp04-021.png'
    for number in ['CP04-02', 'CP04-021EN', 'CP04-022']:
        assert parse_sev(html, number) == []
    assert parse_sev('<form id="searchForm"></form>', 'CP04-02') == []
    with pytest.raises(ValueError):
        parse_sev('<html>error</html>', 'CP04-021')


def test_sev_japanese_api(client, monkeypatch):
    def get(self, url, **kwargs):
        assert url == 'https://shadowverse-evolve.com/cardlist/'
        assert kwargs['params'] == {'cardno': 'CP04-021'}
        return httpx.Response(200, text=Path('tests/fixtures/official_sev.html').read_text(encoding='utf-8'), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    response = client.request('GET', '/api/official/sev/search?word=CP04-021')
    assert response.status_code == 200
    assert response.json()['items'][0]['rarity'] == 'LG'
    assert client.request('GET', '/api/official/sev/search?word=%20').status_code == 422
    client.headers.pop('Authorization')
    assert client.request('GET', '/api/official/sev/search?word=CP04-021').status_code == 401


def test_ws_en_real_results():
    from tcg_toolkit.catalog.providers.official import parse_ws_en
    result = parse_ws_en(Path('tests/fixtures/official_ws_en.html').read_text(encoding='utf-8'))
    assert result['total'] == 97 and len(result['items']) == 5
    first = result['items'][0]
    assert (first['game'], first['card_number'], first['name'], first['rarity']) == (
        'WS EN', 'BD/WE34-E01', '"Astral Harmony" Nanami Hiromachi', 'RR')
    assert first['image_url'] == 'https://en.ws-tcg.com/wordpress/wp-content/images/cardimages/GBP21/MRAS/WE34_E01.png'
    assert result['items'][1]['rarity'] == 'SP'
    assert result['items'][3]['rarity'] == 'OFR'
    assert parse_ws_en('<p class="c-search__results-item"><span>0</span></p>') == {'items': [], 'total': 0}
    with pytest.raises(ValueError):
        parse_ws_en('<html>error</html>')


def test_ws_en_api(client, monkeypatch):
    def get(self, url, **kwargs):
        assert url == 'https://en.ws-tcg.com/cardlist/searchresults/'
        assert kwargs['params'] == {'keyword': 'BD/WE34'}
        return httpx.Response(200, text=Path('tests/fixtures/official_ws_en.html').read_text(encoding='utf-8'), request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    response = client.request('GET', '/api/official/ws_en/search?word=BD%2FWE34')
    assert response.status_code == 200 and response.json()['total'] == 97
    assert client.request('GET', '/api/official/ws_en/search?word=%20').status_code == 422
    client.headers.pop('Authorization')
    assert client.request('GET', '/api/official/ws_en/search?word=BD%2FWE34').status_code == 401
