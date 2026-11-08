"""Account-scoped saved card snapshots and explicit upstream refresh."""
import json
import time
import uuid
from urllib.parse import urlsplit

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from .db_models import Favorite, FavoriteGroup, Subscription, User

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from .auth.routes import current_user, write_guard
from .models import Game, StockStatus
from .errors import SearchRequestError, SearchParseError
from .yuyutei_cart import CartTarget


router = APIRouter(prefix='/api/favorites')


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
    def product_location(self):
        if not self.name.strip() or not self.card_number.strip():
            raise ValueError('Name and card number must not be blank')
        url = urlsplit(self.product_url)
        code = {Game.WSR: 'wsr', Game.LYCEE: 'lo', Game.CHAOS: 'chaos', Game.WS: 'ws', Game.PM: 'pm', Game.SEV: 'sev'}[self.game]
        prefix = f'/sell/{code}/card/'
        if (url.scheme != 'https' or url.netloc != 'yuyu-tei.jp'
                or not url.path.startswith(prefix) or not url.path[len(prefix):]
                or url.query or url.fragment or any(p in ('.', '..') for p in url.path.split('/'))):
            raise ValueError('Invalid product URL for this game')
        return self


class FavoriteInput(CardSnapshot):
    group_id: str | None = Field(default=None, min_length=1)
    cart: CartTarget | None = None


class GroupName(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class GroupTarget(BaseModel):
    # null removes the favorite from its group.
    group_id: str | None = Field(min_length=1)


def store(request):
    return request.app.state.auth.store


def record(row: Favorite):
    return {'id': row.id, 'group_id': row.group_id, 'item': json.loads(row.snapshot),
            'created_at': row.created_at, 'refreshed_at': row.refreshed_at}


def owned(db: Session, user_id, favorite_id, *, lock=False):
    query = select(Favorite).where(Favorite.user_id == user_id, Favorite.id == favorite_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail='favorite_not_found')
    return row


def lock_user(db: Session, user_id):
    # Serialize membership writes with group deletion before taking favorite locks.
    db.execute(select(User.id).where(User.id == user_id).with_for_update()).one()


def owned_group(db: Session, user_id, group_id):
    row = db.scalar(select(FavoriteGroup).where(
        FavoriteGroup.user_id == user_id, FavoriteGroup.id == group_id))
    if row is None:
        raise HTTPException(404, detail='favorite_group_not_found')
    return row


def group_record(row: FavoriteGroup):
    return {'id': row.id, 'name': row.name, 'created_at': row.created_at}


def require_available_name(db: Session, user_id, name, group_id=None):
    query = select(FavoriteGroup.id).where(FavoriteGroup.user_id == user_id, FavoriteGroup.name == name)
    if group_id is not None:
        query = query.where(FavoriteGroup.id != group_id)
    if db.scalar(query) is not None:
        raise HTTPException(409, detail='favorite_group_name_taken')


@router.get('/groups')
def groups(request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        rows = db.scalars(select(FavoriteGroup).where(FavoriteGroup.user_id == user['id']).order_by(
            FavoriteGroup.created_at, FavoriteGroup.id))
        return {'items': [group_record(row) for row in rows]}


@router.post('/groups', status_code=201, dependencies=[Depends(write_guard)])
def create_group(body: GroupName, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        lock_user(db, user['id'])
        require_available_name(db, user['id'], body.name)
        row = FavoriteGroup(id=str(uuid.uuid4()), user_id=user['id'], name=body.name, created_at=int(time.time()))
        db.add(row)
        return group_record(row)


@router.put('/groups/{group_id}', dependencies=[Depends(write_guard)])
def rename_group(group_id: str, body: GroupName, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        lock_user(db, user['id'])
        row = owned_group(db, user['id'], group_id)
        require_available_name(db, user['id'], body.name, row.id)
        row.name = body.name
        return group_record(row)


@router.delete('/groups/{group_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove_group(group_id: str, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        lock_user(db, user['id'])
        row = owned_group(db, user['id'], group_id)
        # Deleting a group keeps its favorites; they just become ungrouped.
        db.execute(update(Favorite).where(Favorite.user_id == user['id'], Favorite.group_id == row.id)
                   .values(group_id=None))
        db.delete(row)


@router.get('')
def listing(request: Request, group_id: str | None = None, user=Depends(current_user)):
    with store(request).session() as db:
        query = select(Favorite).where(Favorite.user_id == user['id'])
        if group_id is not None:
            owned_group(db, user['id'], group_id)
            query = query.where(Favorite.group_id == group_id)
        rows = db.scalars(query.order_by(
            Favorite.created_at.desc(), Favorite.sort_id.desc()))
        return {'items': [record(row) for row in rows]}


@router.post('', dependencies=[Depends(write_guard)])
def add(body: FavoriteInput, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else owned_group(db, user['id'], body.group_id).id
        excluded = {'group_id'} if body.cart is not None else {'group_id', 'cart'}
        db.execute(insert(Favorite).values(
            id=str(uuid.uuid4()), user_id=user['id'], group_id=group_id, game=body.game.value,
            product_url=body.product_url, snapshot=body.model_dump_json(exclude=excluded), created_at=int(time.time())
        ).on_conflict_do_nothing(index_elements=[Favorite.user_id, Favorite.game, Favorite.product_url]))
        row = db.scalar(select(Favorite).where(Favorite.user_id == user['id'],
                        Favorite.game == body.game.value, Favorite.product_url == body.product_url))
        return record(row)


@router.put('/{favorite_id}/group', dependencies=[Depends(write_guard)])
def move(favorite_id: str, body: GroupTarget, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else owned_group(db, user['id'], body.group_id).id
        row = owned(db, user['id'], favorite_id, lock=True)
        row.group_id = group_id
        return record(row)


@router.delete('/{favorite_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove(favorite_id: str, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        row = owned(db, user['id'], favorite_id, lock=True)
        db.execute(delete(Subscription).where(
            Subscription.user_id == row.user_id,
            Subscription.game == row.game,
            Subscription.product_url == row.product_url.rstrip('/'),
        ))
        db.delete(row)


@router.post('/{favorite_id}/refresh', dependencies=[Depends(write_guard)])
def refresh(favorite_id: str, request: Request, user=Depends(current_user)):
    from .web import SEARCHERS
    with store(request).session() as db:
        saved = record(owned(db, user['id'], favorite_id))['item']
    condition = (saved.get('cart') or {}).get('kizu')
    match = None
    try:
        with SEARCHERS[Game(saved['game'])]() as searcher:
            for page in searcher.iter_pages(saved['card_number']):
                match = next((item for item in page.items if item.product_url == saved['product_url']
                              and (condition is None or (item.cart or {}).get('kizu', 0) == condition)), None)
                if match is not None:
                    break
    except SearchRequestError as exc:
        raise HTTPException(504 if isinstance(exc.__cause__, httpx.TimeoutException) else 502,
                            detail='refresh_source_error') from exc
    except SearchParseError as exc:
        raise HTTPException(502, detail='refresh_parse_error') from exc
    if match is None:
        raise HTTPException(404, detail='refresh_product_missing')
    with store(request).session() as db:
        row = owned(db, user['id'], favorite_id, lock=True)
        row.snapshot, row.refreshed_at = json.dumps(match.to_dict()), int(time.time())
        return record(row)
