"""Merchant-specific, read-only card price lookup."""
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AfterValidator

from ..auth.dependencies import current_user
from .errors import ProductSourceError, SearchParseError, SearchRequestError
from .games import GAME_NAMES
from .identity import normalize_favorite_url
from .models import Game, SearchSource
from .providers.rarity import ws_catalog_search
from .search import SEARCH_SOURCES, get_searcher_type
from .providers.yuyutei_products import fetch_product_snapshot

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
    game = GAME_NAMES.get(value, value).casefold().replace(' ', '_')
    return 'chaos' if game == 'chaostcg' else game


def unique_hobby_printing(game, suffix, wanted):
    cards, total = ws_catalog_search(game, suffix, 5)
    matches = {c['card_number'].casefold() for c in cards
               if c['card_number'].partition('/')[2].casefold() == suffix.casefold()}
    return total == len(cards) and matches == {wanted}


def merchant_prices(source: SearchSource, game: Game, card_number: str) -> list[dict]:
    items, seen = [], set()
    wanted = card_number.casefold()
    keyword = card_number
    alias = None
    alias_matches = None
    series, _, suffix = card_number.partition('/')
    if source == SearchSource.HOBBYSTATION and game in (Game.WS, Game.WSR) and series and suffix:
        keyword = suffix
        alias = ('WS-' if game == Game.WS else 'WR-') + keyword
    try:
        with get_searcher_type(source, game)() as searcher:
            for page in searcher.iter_pages(keyword):
                for item in page.items:
                    if item.condition == 'damaged':
                        continue
                    number = item.card_number.strip().casefold()
                    if number != wanted:
                        if not alias or number != alias.casefold():
                            continue
                        # HS omits the series. Only use its listing if the official
                        # catalog identifies this exact printing uniquely by suffix.
                        if alias_matches is None:
                            alias_matches = unique_hobby_printing(game, keyword, wanted)
                        if not alias_matches:
                            continue
                    url = normalize_favorite_url(source, game, item.product_url)
                    if url in seen:
                        continue
                    if source == SearchSource.YUYUTEI and item.condition != 'normal':
                        offer = fetch_product_snapshot(game, url)
                        if offer['card_number'].strip().casefold() != wanted:
                            raise ProductSourceError('product_unreadable')
                    elif item.condition == 'normal':
                        if source == SearchSource.CARDMAX:
                            item = searcher.read_item_stock(item, keyword=keyword, page=page.page)
                        offer = item.to_dict()
                    else:
                        continue
                    items.append({**{key: offer[key] for key in OFFER_FIELDS}, 'product_url': url,
                                  'card_number': card_number if number != wanted else offer['card_number'],
                                  **({'stock_text': item.stock_text} if source == SearchSource.HOBBYSTATION else {}),
                                  'condition': 'normal'})
                    seen.add(url)
    except httpx.TimeoutException:
        raise HTTPException(504, detail='price_source_timeout') from None
    except httpx.HTTPError:
        raise HTTPException(502, detail='price_source_unavailable') from None
    except SearchRequestError as exc:
        timeout = isinstance(exc.__cause__, httpx.TimeoutException)
        raise HTTPException(504 if timeout else 502,
                            detail='price_source_timeout' if timeout else 'price_source_unavailable') from None
    except (SearchParseError, ValueError):
        raise HTTPException(502, detail='price_source_unreadable') from None
    except ProductSourceError as exc:
        errors = {'product_timeout': 'price_source_timeout',
                  'product_unavailable': 'price_source_unavailable',
                  'product_unreadable': 'price_source_unreadable'}
        raise HTTPException(exc.status_code, detail=errors[exc.code]) from None
    return items


MERCHANTS = {SearchSource.YUYUTEI: '游游亭', SearchSource.HOBBYSTATION: 'Hobby Station',
             SearchSource.CARDMAX: 'Card Max'}


@router.get('/merchants')
def merchants():
    return {'items': [{'id': key, 'name': name, 'games': list(SEARCH_SOURCES[key])}
                      for key, name in MERCHANTS.items()]}


@router.get('')
def lookup(merchant: str, game: str,
           card_number: Annotated[str, Query(), AfterValidator(clean_card_number)]):
    if merchant not in MERCHANTS:
        raise HTTPException(422, detail='unsupported_merchant')
    game = canonical_game(game)
    if game not in SEARCH_SOURCES[merchant]:
        raise HTTPException(422, detail='unsupported_merchant_game')
    return {'merchant': merchant, 'game': game, 'card_number': card_number,
            'items': merchant_prices(SearchSource(merchant), Game(game), card_number)}
