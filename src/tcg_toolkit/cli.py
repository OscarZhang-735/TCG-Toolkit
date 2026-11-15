"""Command-line keyword search for manual testing."""

import argparse
import json
import sys
import unicodedata

from .errors import SearchError
from .search import SEARCHERS


def _width(text: str) -> int:
    return sum(0 if unicodedata.combining(c) else
               2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def print_table(game, page) -> None:
    print(f"{game} | {len(page.items)} results")
    if not page.items:
        print("No results found.")
        return
    rows = [["Card Number", "Name", "Price (JPY)", "Stock"]]
    for item in page.items:
        stock = {"in_stock": "In stock", "out_of_stock": "Out of stock", "unknown": "Unknown"}[item.stock_status]
        if item.stock_quantity is not None:
            stock += f" ({item.stock_quantity})"
        rows.append([item.card_number, item.name, f"{item.price:,}", stock])
    rows = [[" ".join(value.split()) for value in row] for row in rows]
    widths = [max(_width(row[i]) for row in rows) for i in range(4)]
    for index, row in enumerate(rows):
        print(" | ".join(value + " " * (width - _width(value))
                         for value, width in zip(row, widths)))
        if index == 0:
            print("-+-".join("-" * width for width in widths))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="搜索游游亭卡牌，输出 JSON")
    parser.add_argument("keyword", help="关键词")
    parser.add_argument("-g", "--game", action="append", required=True, choices=tuple(SEARCHERS),
                        help="搜索器，可重复指定；多个搜索器使用同一关键词")
    parser.add_argument("--clean", action="store_true", help="以易读表格输出结果")
    parser.add_argument("--page", type=int, default=1, help="页码或遍历起始页（默认 1）")
    parser.add_argument("--all", action="store_true", help="从指定页遍历至末页，每页输出一行 JSON")
    args = parser.parse_args(argv)
    games = list(dict.fromkeys(args.game))
    if not args.keyword.strip():
        parser.error("Keyword must not be empty" if args.clean else "关键词不能为空")
    if args.page < 1:
        parser.error("Page must be a positive integer" if args.clean else "页码必须为正整数")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    status = 0
    for game in games:
        searcher_type = SEARCHERS[game]
        try:
            with searcher_type() as searcher:
                pages = (searcher.iter_pages(args.keyword, start_page=args.page) if args.all
                         else [searcher.search_page(args.keyword, page=args.page)])
                for page in pages:
                    if args.clean:
                        print_table(game, page)
                    else:
                        data = page.to_dict()
                        if len(games) > 1:
                            data["game"] = game
                        print(json.dumps(data, ensure_ascii=False,
                                         indent=None if args.all or len(games) > 1 else 2), flush=True)
        except SearchError as exc:
            message = (f"Search failed for {game}: {type(exc).__name__}" if args.clean
                       else f"搜索失败：{exc}")
            print(message, file=sys.stderr)
            status = 1
    return status
