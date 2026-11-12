"""Public, JSON-compatible search results."""

from dataclasses import asdict, dataclass
from enum import StrEnum


class Game(StrEnum):
    SEV = "sev"
    WS = "ws"
    PM = "pm"
    WSR = "wsr"
    LYCEE = "lycee"
    CHAOS = "chaos"


class StockStatus(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SearchItem:
    game: Game
    name: str
    card_number: str
    price: int
    currency: str
    stock_status: StockStatus
    stock_quantity: int | None
    stock_text: str
    product_url: str
    image_url: str | None = None
    rarity: str | None = None
    # YuYu-Tei cart identity from the hidden cart_* fields: gid, ver, cid, kizu.
    cart: dict | None = None
    # Explicit source condition for price lookup; not part of the search API.
    condition: str | None = None

    def to_dict(self) -> dict:
        result = asdict(self)
        result.pop('condition')
        result["game"] = self.game.value
        result["stock_status"] = self.stock_status.value
        return result


@dataclass(frozen=True)
class SearchPage:
    items: list[SearchItem]
    keyword: str
    page: int
    next_page: int | None

    @property
    def has_next(self) -> bool:
        return self.next_page is not None

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "keyword": self.keyword,
            "page": self.page,
            "next_page": self.next_page,
            "has_next": self.has_next,
        }
