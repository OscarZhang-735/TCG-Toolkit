import json

import pytest

from tcg_toolkit import Game, SearchPage, SearchRequestError
from tcg_toolkit import cli
from tcg_toolkit.catalog.search import SEARCHERS


@pytest.fixture
def calls(monkeypatch):
    calls = []
    class Fake:
        def __init__(self, *, stock): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def search_page(self, keyword, *, page):
            calls.append((keyword, page))
            return SearchPage([], keyword, page, None)
    for game in SEARCHERS:
        monkeypatch.setitem(SEARCHERS, game, Fake)
    return calls


def test_multiple_games_json(calls, capsys):
    assert cli.main(["YZ", "-g", "wsr", "-g", "chaos", "-g", "wsr", "--page", "2"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["game"] for row in rows] == ["wsr", "chaos"]
    assert calls == [("YZ", 2), ("YZ", 2)]


def test_continue_after_error(calls, capsys, monkeypatch):
    class Broken:
        def __init__(self, *, stock): pass
        def __enter__(self):
            raise SearchRequestError("failed", game=Game.WSR, keyword="X", page=1, url="test")
        def __exit__(self, *args): pass
    monkeypatch.setitem(SEARCHERS, Game.WSR, Broken)
    assert cli.main(["X", "-g", "wsr", "-g", "lycee"]) == 1
    output = capsys.readouterr()
    assert "搜索失败" in output.err
    assert json.loads(output.out)["game"] == "lycee"
    assert calls == [("X", 1)]
