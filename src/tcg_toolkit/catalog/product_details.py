"""Authenticated access to current public merchant product details and stock."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import current_user
from .errors import ProductSourceError
from .identity import GAME_PATHS
from .models import SearchSource
from .providers.storefront_details import fetch_storefront_detail
from .providers.yuyutei_products import fetch_product

router = APIRouter(prefix='/api', dependencies=[Depends(current_user)])


@router.get('/yuyutei/product')
def product(gid: Annotated[int, Query()], ver: Annotated[str, Query(min_length=1, max_length=64,
            pattern=r'^[A-Za-z0-9_-][A-Za-z0-9_.-]*$')], cid: Annotated[str, Query(min_length=1, max_length=32,
            pattern=r'^[0-9]+$')], stock: bool = False, kizu: Annotated[int, Query(ge=0, le=1)] = 0):
    if gid not in GAME_PATHS:
        raise HTTPException(422, detail='invalid_product')
    try:
        details = fetch_product(gid, ver, cid, stock=stock, kizu=kizu)
    except ProductSourceError as exc:
        raise HTTPException(exc.status_code, detail=exc.code) from None
    return {'gid': gid, 'ver': ver, 'cid': cid, **details}


@router.get('/{source}/product/stock')
def storefront_stock(source: Literal['hobbystation', 'cardmax'],
                     product_url: Annotated[str, Query(min_length=1, max_length=512)]):
    try:
        detail = fetch_storefront_detail(SearchSource(source), product_url)
    except ValueError:
        raise HTTPException(422, detail='invalid_product') from None
    except ProductSourceError as exc:
        raise HTTPException(exc.status_code, detail=exc.code) from None
    return {key: detail[key] for key in ('stock_status', 'stock_quantity', 'stock_text', 'game', 'card_number', 'rarity')}
