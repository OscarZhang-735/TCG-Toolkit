import json
from pathlib import Path

import httpx
import pytest

from tcg_toolkit.catalog.providers import official
from test_cards import client


def fixture(name):
    return (Path(__file__).parent / 'fixtures' / name).read_text(encoding='utf-8')


def test_lycee_printings():
    items = official.parse_lycee(fixture('official_lycee.html'))
    assert [(x['card_number'], x['rarity']) for x in items] == [('LO-1606-S', 'SP'), ('LO-1606', 'SR')]


def test_ws_versions():
    items = official.parse_ws(json.loads(fixture('official_ws.json')))
    assert [x['card_number'] for x in items] == ['BD/WE49-56', 'BD/WE49-56BDR', 'BD/WE49-56SP']
    assert [x['rarity'] for x in items] == ['N', 'BDR', 'SP']


def test_wsr_api(client, monkeypatch):
    detail = fixture('official_wsr_detail.html')

    def get(self, url, *, params, **kwargs):
        if url == official.WSR_SEARCH:
            assert params == {'keyword': 'OS13/R13-001'}
            text = fixture('official_wsr.html')
        else:
            assert url == official.WSR_DETAIL
            number = params['cardno']
            rarity = {'OS13/R13-001': 'RR', 'OS13/R13-001OFR': 'OFR', 'OS13/R13-001SEC': 'SEC'}[number]
            text = detail.replace('OS13/R13-001', number).replace('RR', rarity)
        return httpx.Response(200, text=text, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx.Client, 'get', get)
    response = client.request('GET', '/api/official/wsr/search?word=OS13/R13-001')
    assert response.status_code == 200
    assert [r['rarity'] for r in response.json()['items']] == ['RR', 'OFR', 'SEC']
    client.headers.pop('Authorization')
    assert client.request('GET', '/api/official/wsr/search?word=test').status_code == 401


def test_ws_unreadable_response(client, monkeypatch):
    def get(self, url, **kwargs):
        return httpx.Response(200, json={}, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx.Client, 'get', get)
    assert client.request('GET', '/api/official/ws/search?word=WE49-56').status_code == 502


def test_pm_metadata_and_duplicate_numbers():
    items = official.parse_pm(fixture('official_pm.html'))
    assert len(items) == 6
    assert all(x['game'] == 'PM' and x['card_number'] == '01-001' for x in items)
    assert items[0]['rarity'] == 'UC' and items[1]['rarity'] == 'R'
    assert items[0]['name'] != items[1]['name']


@pytest.mark.parametrize('parse,file,number,game,name,rarity', [
    (official.parse_sev, 'official_sev.html', 'CP04-021', 'SEV', 'クリスティーナ', 'LG'),
    (official.parse_sev_en, 'official_sev_en.html', 'CP04-021EN', 'SEV EN', 'Christina', 'L'),
])
def test_sev_exact_printing(parse, file, number, game, name, rarity):
    html = fixture(file)
    [card] = parse(html, number)
    assert (card['game'], card['name'], card['rarity']) == (game, name, rarity)
    assert parse(html, 'CP04-022') == []


def test_ws_en_real_results():
    result = official.parse_ws_en(fixture('official_ws_en.html'))
    assert result['total'] == 97 and len(result['items']) == 5
    first = result['items'][0]
    assert (first['game'], first['card_number'], first['name'], first['rarity']) == (
        'WS EN', 'BD/WE34-E01', '"Astral Harmony" Nanami Hiromachi', 'RR')
