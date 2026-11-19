"""Account-scoped saved card snapshots and explicit upstream refresh."""
import json
import time
import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from ..auth.dependencies import current_user, write_guard
from ..cart.schemas import CartTarget
from ..catalog.errors import SearchRequestError, SearchParseError
from ..catalog.hobby_images import public_hobby_image_url
from ..catalog.identity import favorite_source, normalize_favorite_url
from ..catalog.models import Game, SearchSource, StockStatus
from ..catalog.search import create_searcher
from ..persistence.models import Favorite, FavoriteGroup, Subscription
from .group_routes import GroupTarget, group_router
from .groups import CollectionGroups, lock_user

router = APIRouter(prefix='/api/favorites')
collection_groups = CollectionGroups(FavoriteGroup, Favorite, 'favorite_group')
router.include_router(group_router(collection_groups))


class CardSnapshot(BaseModel):
    game: Game
    name: str = Field(min_length=1)
    card_number: str = Field(min_length=1)
    price: int = Field(ge=0)
    currency: str = Field(pattern='^JPY$')
    stock_status: StockStatus
    stock_quantity: int | None = Field(default=None, ge=0)
    stock_text: str
    product_url: str
    image_url: str | None = None
    rarity: str | None = None

    @model_validator(mode='after')
    def nonblank_card_fields(self):
        if not self.name.strip() or not self.card_number.strip():
            raise ValueError('Name and card number must not be blank')
        return self


class FavoriteInput(CardSnapshot):
    source: SearchSource | None = None
    group_id: str | None = Field(default=None, min_length=1)
    cart: CartTarget | None = None

    @model_validator(mode='after')
    def favorite_location(self):
        source = self.source or favorite_source(self.product_url)
        url = normalize_favorite_url(source, self.game, self.product_url)
        if source != SearchSource.YUYUTEI:
            if self.cart is not None:
                raise ValueError('Cart targets are only supported for yuyutei')
            self.product_url = url
        return self


def record(row: Favorite, request: Request):
    source = favorite_source(row.product_url)
    item = json.loads(row.snapshot)
    if source == SearchSource.HOBBYSTATION:
        item['image_url'] = public_hobby_image_url(request, item.get('image_url'))
    return {'id': row.id, 'group_id': row.group_id, 'source': source, 'item': item,
            'created_at': row.created_at, 'refreshed_at': row.refreshed_at}


def owned(db: Session, user_id, favorite_id, *, lock=False):
    query = select(Favorite).where(Favorite.user_id == user_id, Favorite.id == favorite_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail='favorite_not_found')
    return row


@router.get('')
def listing(request: Request, group_id: str | None = None, user=Depends(current_user)):
    with request.app.state.database.session() as db:
        query = select(Favorite).where(Favorite.user_id == user['id'])
        if group_id is not None:
            collection_groups.owned(db, user['id'], group_id)
            query = query.where(Favorite.group_id == group_id)
        rows = db.scalars(query.order_by(
            Favorite.created_at.desc(), Favorite.sort_id.desc()))
        return {'items': [record(row, request) for row in rows]}


@router.post('', dependencies=[Depends(write_guard)])
def add(body: FavoriteInput, request: Request, user=Depends(current_user)):
    with request.app.state.database.session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else collection_groups.owned(db, user['id'], body.group_id).id
        excluded = {'group_id', 'source'}
        if body.cart is None:
            excluded.add('cart')
        db.execute(insert(Favorite).values(
            id=str(uuid.uuid4()), user_id=user['id'], group_id=group_id, game=body.game.value,
            product_url=body.product_url, snapshot=body.model_dump_json(exclude=excluded), created_at=int(time.time())
        ).on_conflict_do_nothing(index_elements=[Favorite.user_id, Favorite.game, Favorite.product_url]))
        row = db.scalar(select(Favorite).where(Favorite.user_id == user['id'],
                        Favorite.game == body.game.value, Favorite.product_url == body.product_url))
        return record(row, request)


@router.put('/{favorite_id}/group', dependencies=[Depends(write_guard)])
def move(favorite_id: str, body: GroupTarget, request: Request, user=Depends(current_user)):
    with request.app.state.database.session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else collection_groups.owned(db, user['id'], body.group_id).id
        row = owned(db, user['id'], favorite_id, lock=True)
        row.group_id = group_id
        return record(row, request)


@router.delete('/{favorite_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove(favorite_id: str, request: Request, user=Depends(current_user)):
    with request.app.state.database.session() as db:
        row = owned(db, user['id'], favorite_id, lock=True)
        db.execute(delete(Subscription).where(
            Subscription.user_id == row.user_id,
            Subscription.game == row.game,
            Subscription.product_url == row.product_url.rstrip('/'),
        ))
        db.delete(row)


@router.post('/{favorite_id}/refresh', dependencies=[Depends(write_guard)])
def refresh(favorite_id: str, request: Request, user=Depends(current_user)):
    with request.app.state.database.session() as db:
        row = owned(db, user['id'], favorite_id)
        saved = json.loads(row.snapshot)
        source = favorite_source(row.product_url)
        product_url = normalize_favorite_url(source, row.game, row.product_url)
    condition = (saved.get('cart') or {}).get('kizu')
    match = None
    try:
        with create_searcher(source, saved['game']) as searcher:
            for page in searcher.iter_pages(saved['card_number']):
                match = next((item for item in page.items if item.product_url.rstrip('/') == product_url
                              and (condition is None or (item.cart or {}).get('kizu', 0) == condition)), None)
                if match is not None:
                    match = searcher.read_item_stock(match, keyword=page.keyword, page=page.page)
                    break
    except SearchRequestError as exc:
        raise HTTPException(504 if isinstance(exc.__cause__, httpx.TimeoutException) else 502,
                            detail='refresh_source_error') from exc
    except SearchParseError as exc:
        raise HTTPException(502, detail='refresh_parse_error') from exc
    if match is None:
        raise HTTPException(404, detail='refresh_product_missing')
    with request.app.state.database.session() as db:
        row = owned(db, user['id'], favorite_id, lock=True)
        row.snapshot = json.dumps({**match.to_dict(), 'product_url': row.product_url})
        row.refreshed_at = int(time.time())
        return record(row, request)
