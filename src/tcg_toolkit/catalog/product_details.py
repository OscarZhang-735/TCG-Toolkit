"""HTTP endpoint for public YuYu-Tei product details and stock."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import current_user
from .errors import ProductSourceError
from .identity import GAME_PATHS
from .providers.yuyutei_products import fetch_product

router = APIRouter(prefix='/api/yuyutei', dependencies=[Depends(current_user)])


@router.get('/product')
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
