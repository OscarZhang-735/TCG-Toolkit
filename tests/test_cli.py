import json

import pytest

from tcg_toolkit import Game, SearchPage, SearchRequestError
from tcg_toolkit import cli
from tcg_toolkit.search import SEARCHERS, HobbyStationLyceeSearcher


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
        def iter_pages(self, keyword, *, start_page):
            yield self.search_page(keyword, page=start_page)
    for game in SEARCHERS:
        monkeypatch.setitem(SEARCHERS, game, Fake)
    return calls


def test_multiple_games_json(calls, capsys):
    assert cli.main(["YZ", "-g", "wsr", "-g", "chaos", "-g", "wsr", "--page", "2"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["game"] for row in rows] == ["wsr", "chaos"]
    assert calls == [("YZ", 2), ("YZ", 2)]


def test_clean_all(calls, capsys):
    assert cli.main(["OS01", "--game", "wsr", "--clean", "--all"]) == 0
    output = capsys.readouterr().out
    assert "wsr | 0 results" in output and "No results found." in output
    assert "page" not in output.lower() and "第" not in output
    assert calls == [("OS01", 1)]


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


def test_table_real_items(capsys):
    from pathlib import Path
    from tcg_toolkit import WSRSearcher
    html = (Path(__file__).parent / "fixtures/wsr_1.html").read_text(encoding="utf-8")
    with WSRSearcher() as searcher:
        page = searcher.parse_page(html, keyword="OS01", page=1, url=searcher.base_url)
    cli.print_table("wsr", page)
    output = capsys.readouterr().out
    assert "OS01/R01-023A" in output and "548,000" in output
    assert "Out of stock (0)" in output and "Name" in output
    assert cli._width("中文AB") == 6


@pytest.mark.parametrize("game", list(Game))
def test_registered_games(game, calls, capsys):
    assert cli.main(["01", "--game", game.value, "--clean"]) == 0
    assert f"{game} | 0 results" in capsys.readouterr().out
    assert calls == [("01", 1)]


def test_game_option_is_required(calls):
    with pytest.raises(SystemExit) as exc:
        cli.main(["wsr", "01"])
    assert exc.value.code == 2
    assert calls == []


def test_hobby_source(monkeypatch, capsys):
    def search(self, keyword, *, page):
        return SearchPage([], keyword, page, None, self.source)
    monkeypatch.setattr(HobbyStationLyceeSearcher, 'search_page', search)
    assert cli.main(['LO-4899', '--source', 'hobbystation', '-g', 'lycee']) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data['source'], data['keyword']) == ('hobbystation', 'LO-4899')
    with pytest.raises(SystemExit) as exc:
        cli.main(['LO-4899', '--source', 'hobbystation', '-g', 'sev'])
    assert exc.value.code == 2


def test_new_games_combined(calls, capsys):
    assert cli.main(["01", "-g", "ws", "-g", "pm"]) == 0
    assert [json.loads(line)["game"] for line in capsys.readouterr().out.splitlines()] == ["ws", "pm"]
