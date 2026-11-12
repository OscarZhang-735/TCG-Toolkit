"""Merchant-specific, read-only card price lookup."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AfterValidator

from .auth.routes import current_user
from .cards import GAMES
from .errors import SearchParseError, SearchRequestError
from .models import Game
from .product_details import fetch_product_snapshot, normalize_product_url

router = APIRouter(prefix='/api/prices', dependencies=[Depends(current_user)])
OFFER_FIELDS = ('name', 'card_number', 'rarity', 'price', 'currency',
                'stock_status', 'stock_quantity', 'product_url')


def clean_card_number(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 100:
        raise ValueError('Card number must contain 1–100 characters')
    return value


def canonical_game(value: str) -> str:
    value = value.strip().casefold()
    game = GAMES.get(value, value).casefold().replace(' ', '_')
    return 'chaos' if game == 'chaostcg' else game


def yuyutei_prices(game: str, card_number: str) -> list[dict]:
    from .web import SEARCHERS

    items, seen = [], set()
    wanted = card_number.casefold()
    try:
        with SEARCHERS[Game(game)]() as searcher:
            for page in searcher.iter_pages(card_number):
                for item in page.items:
                    if item.card_number.strip().casefold() != wanted or item.condition == 'damaged':
                        continue
                    url = normalize_product_url(game, item.product_url)
                    if url in seen:
                        continue
                    if item.condition == 'normal':
                        offer = item.to_dict()
                    else:
                        offer = fetch_product_snapshot(game, url)
                        if offer['card_number'].strip().casefold() != wanted:
                            raise HTTPException(502, detail='price_source_unreadable')
                    items.append({**{key: offer[key] for key in OFFER_FIELDS},
                                  'product_url': url, 'condition': 'normal'})
                    seen.add(url)
    except SearchRequestError as exc:
        timeout = isinstance(exc.__cause__, httpx.TimeoutException)
        raise HTTPException(504 if timeout else 502,
                            detail='price_source_timeout' if timeout else 'price_source_unavailable') from None
    except (SearchParseError, ValueError):
        raise HTTPException(502, detail='price_source_unreadable') from None
    except HTTPException as exc:
        errors = {'product_timeout': 'price_source_timeout',
                  'product_unavailable': 'price_source_unavailable',
                  'product_unreadable': 'price_source_unreadable'}
        raise HTTPException(exc.status_code, detail=errors.get(exc.detail, exc.detail)) from None
    return items


@dataclass(frozen=True)
class Merchant:
    name: str
    games: tuple[str, ...]
    lookup: Callable[[str, str], list[dict]]


MERCHANTS = {'yuyutei': Merchant('游游亭', ('ws', 'wsr', 'pm', 'sev', 'lycee', 'chaos'), yuyutei_prices)}


@router.get('/merchants')
def merchants():
    return {'items': [{'id': key, 'name': merchant.name, 'games': list(merchant.games)}
                      for key, merchant in MERCHANTS.items()]}


@router.get('')
def lookup(merchant: str, game: str,
           card_number: Annotated[str, Query(), AfterValidator(clean_card_number)]):
    provider = MERCHANTS.get(merchant)
    if provider is None:
        raise HTTPException(422, detail='unsupported_merchant')
    game = canonical_game(game)
    if game not in provider.games:
        raise HTTPException(422, detail='unsupported_merchant_game')
    return {'merchant': merchant, 'game': game, 'card_number': card_number,
            'items': provider.lookup(game, card_number)}
