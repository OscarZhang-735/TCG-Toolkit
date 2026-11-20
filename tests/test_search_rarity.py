from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from tcg_toolkit.catalog.models import Game, SearchItem, SearchPage, SearchSource, StockStatus
from tcg_toolkit.catalog.providers.rarity import fill_rarities


@pytest.mark.parametrize('shop,game,number,files,expected', [
    ('hobbystation', 'ws', 'WS-WE49-56SP', ['official_ws.json'], 'SP'),
    ('hobbystation', 'ws', 'WS-WE49-56S', ['official_ws.json'], None),
    ('hobbystation', 'wsr', 'WR-R13-001', ['official_wsr.html', 'official_wsr_detail.html'], 'RR'),
    ('cardmax', 'sev', 'CP04-021', ['official_sev.html'], 'LG'),
])
def test_official_printing(monkeypatch, shop, game, number, files, expected):
    responses = list(files)
    def get(url, **kwargs):
        body = (Path(__file__).parent / 'fixtures' / responses.pop(0)).read_bytes()
        return httpx.Response(200, content=body, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', get)
    game = Game(game)
    item = SearchItem(game, 'same title', number, 10, 'JPY', StockStatus.UNKNOWN, None, '', '')
    page = SearchPage([item, item, replace(item, rarity='PR')], 'search', 1, None, SearchSource(shop))
    result = fill_rarities(page, game, 5)
    assert [i.rarity for i in result.items] == [expected, expected, 'PR']
    assert not responses
