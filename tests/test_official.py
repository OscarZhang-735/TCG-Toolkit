import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

from tcg_toolkit.catalog import official as official_routes
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


@pytest.mark.parametrize('search,search_url,file,selector,number', [
    (official_routes.search_wsr, official.WSR_SEARCH, 'official_wsr.html', '.card-Item', 'OS13/R13-001'),
    (official_routes.search_ws_en, official.WS_EN_SEARCH, 'official_ws_en.html', 'a[href*="?cardno="]', 'BD/WE34-E01'),
])
def test_exact_printing_after_first_five_prefix_matches(monkeypatch, search, search_url, file, selector, number):
    page = BeautifulSoup(fixture(file), 'html.parser')
    exact = page.select_one(selector)
    variants = [number + suffix for suffix in ('S', 'SP', 'SSP', 'SEC', 'OFR')]
    for variant in variants:
        card = deepcopy(exact)
        link = card if card.name == 'a' else card.find('a')
        link['href'] = link['href'].replace('cardno=' + number, 'cardno=' + variant)
        exact.insert_before(card)
    page.select_one('.search-Hits span, .c-search__results-item span').string = '10'

    def get(self, url, *, params):
        if url == search_url:
            assert params == {'keyword': number}
            content = str(page)
        else:
            assert url == official.WSR_DETAIL
            content = fixture('official_wsr_detail.html').replace(number, params['cardno'])
        return httpx.Response(200, text=content, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx.Client, 'get', get)
    result = search(number)
    assert [item['card_number'] for item in result['items']] == [number, *variants[:4]]


@pytest.mark.parametrize('search,search_url,file,selector,number,view', [
    (official_routes.search_wsr, official.WSR_SEARCH, 'official_wsr.html',
     '.card-Item', 'OS13/R13-001', 'image'),
    (official_routes.search_ws_en, official.WS_EN_SEARCH, 'official_ws_en.html',
     'a[href*="?cardno="]', 'BD/WE34-E01', 'text'),
])
def test_purchase_exact_printings_from_later_bushiroad_page(monkeypatch, search, search_url, file, selector, number, view):
    first = BeautifulSoup(fixture(file), 'html.parser')
    links = first.select(selector)
    first.select_one('.search-Hits span, .c-search__results-item span').string = str(len(links) + 1)
    second = str(links[0])

    def get(self, url, *, params):
        if url == search_url:
            content = str(first)
        elif url == official.WSR_DETAIL:
            assert params == {'cardno': number}
            content = fixture('official_wsr_detail.html')
        else:
            assert url.endswith('/cardlist/cardsearch_ex')
            assert params == {'keyword': number, 'view': view, 'page': 2}
            content = second
        return httpx.Response(200, text=content, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx.Client, 'get', get)
    assert [row['card_number'] for row in search(number, exact=True)['items']] == [number, number]


def test_purchase_ws_requires_every_page_even_after_one_exact_match(monkeypatch):
    first = json.loads(fixture('official_ws.json'))
    number = first['items'][0]['card_number']
    pages = {1: {**first, 'total': 4}, 2: {**first, 'items': first['items'][:1], 'total': 4, 'page': 2}}

    def get(self, url, *, params):
        return httpx.Response(200, json=pages[params.get('page', 1)], request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx.Client, 'get', get)
    assert len(official_routes.search_ws(number, exact=True)['items']) == 2
    pages[2]['items'] = []
    with pytest.raises(official_routes.HTTPException) as exc:
        official_routes.search_ws(number, exact=True)
    assert exc.value.status_code == 502


@pytest.mark.parametrize('search,file,number,page_link,next_page,selector', [
    (official_routes.search, 'official_lycee.html', 'LO-1606',
     '<div id="pagemenu"><button name="page" value="2">2</button></div>', 2, 'tr'),
    (official_routes.search_pm, 'official_pm.html', '01-001',
     '<ul class="pager"><li class="pager-next"><a href="?page=1">next</a></li></ul>', 1, 'table tbody tr'),
])
def test_purchase_table_printings_across_pages(monkeypatch, search, file, number, page_link, next_page, selector):
    first = fixture(file)
    second = BeautifulSoup(first, 'html.parser')
    for row in second.select(selector)[:-1]:
        row.decompose()
    first_matches = 1 if file == 'official_lycee.html' else 6

    def get(self, url, *, params):
        if 'page' in params:
            assert params['page'] == next_page
            content = str(second)
        else:
            content = first + page_link
        return httpx.Response(200, text=content, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx.Client, 'get', get)
    items = search(number, exact=True)['items']
    assert [item['card_number'] for item in items] == [number] * (first_matches + 1)
