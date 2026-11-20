from pathlib import Path

import httpx
import pytest

from tcg_toolkit import ChaosTCGSearcher, SearchParseError, SearchRequestError, create_searcher


def fixture(name):
    return (Path(__file__).parent / "fixtures" / f"{name}.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("game,shop_path,number,price,rarity,quantity", [
    ("wsr", "wsr", "OS01/R01-023A", 548000, "AGR", 0),
    ("ws", "ws", "AMG/W121-016EX", 59800, "SEC+", 1),
    ("pm", "pm", "01-011a", 3980, "SP", 0),
    ("lycee", "lo", "LO-1315-X", 69800, "SSP", 0),
    ("chaos", "chaos", "YZ-001", 7980, "SP", 0),
    ("sev", "sev", "BP01-U01", 1780, "UR", 5),
])
def test_yuyutei_game_results(game, shop_path, number, price, rarity, quantity):
    with create_searcher("yuyutei", game) as searcher:
        assert searcher.search_path == f"/sell/{shop_path}/s/search"
        page = searcher.parse_page(fixture(shop_path + "_1"), keyword=number, page=1, url=searcher.base_url)
    item = page.items[0]
    assert (item.game, item.card_number, item.price, item.rarity) == (game, number, price, rarity)
    assert item.stock_quantity == quantity
    assert item.stock_status == ("in_stock" if quantity else "out_of_stock")
    assert item.name and item.currency == "JPY"
    assert item.product_url.endswith(f"/{item.cart['ver']}/{item.cart['cid']}")


def test_empty_result_and_unrecognized_page():
    with create_searcher("yuyutei", "wsr") as searcher:
        params = dict(keyword="missing", page=1, url=searcher.base_url)
        assert searcher.parse_page(fixture("empty"), **params).items == []
        with pytest.raises(SearchParseError):
            searcher.parse_page("<main>temporarily unavailable</main>", **params)


def test_iteration_follows_the_next_page():
    calls = []

    def respond(request):
        page = int(request.url.params.get("page", "1"))
        calls.append(page)
        return httpx.Response(200, text=fixture(f"chaos_{page}"))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        pages = list(ChaosTCGSearcher(client=client, page_interval=0).iter_pages("YZ"))
    assert calls == [1, 2]
    assert [page.next_page for page in pages] == [2, None]


def test_iteration_failure_does_not_report_completion():
    def respond(request):
        if "page" in request.url.params:
            return httpx.Response(503)
        return httpx.Response(200, text=fixture("chaos_1"))
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        pages = ChaosTCGSearcher(client=client, page_interval=0).iter_pages("YZ")
        assert next(pages).items
        with pytest.raises(SearchRequestError) as caught:
            next(pages)
        assert caught.value.page == 2
