"""Account-scoped saved card snapshots and explicit upstream refresh."""
import json
import time
import uuid
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from .db_models import Favorite

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from .auth.routes import current_user, write_guard
from .models import Game, StockStatus
from .errors import SearchRequestError, SearchParseError


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


def store(request):
    return request.app.state.auth.store


def record(row: Favorite):
    return {'id': row.id, 'item': json.loads(row.snapshot),
            'created_at': row.created_at, 'refreshed_at': row.refreshed_at}


def owned(db: Session, user_id, favorite_id, *, lock=False):
    query = select(Favorite).where(Favorite.user_id == user_id, Favorite.id == favorite_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail='favorite_not_found')
    return row


@router.get('')
def listing(request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        rows = db.scalars(select(Favorite).where(Favorite.user_id == user['id']).order_by(
            Favorite.created_at.desc(), Favorite.sort_id.desc()))
        return {'items': [record(row) for row in rows]}


@router.post('', dependencies=[Depends(write_guard)])
def add(body: CardSnapshot, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        db.execute(insert(Favorite).values(
            id=str(uuid.uuid4()), user_id=user['id'], game=body.game.value,
            product_url=body.product_url, snapshot=body.model_dump_json(), created_at=int(time.time())
        ).on_conflict_do_nothing(index_elements=[Favorite.user_id, Favorite.game, Favorite.product_url]))
        row = db.scalar(select(Favorite).where(Favorite.user_id == user['id'],
                        Favorite.game == body.game.value, Favorite.product_url == body.product_url))
        return record(row)


@router.delete('/{favorite_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove(favorite_id: str, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        db.delete(owned(db, user['id'], favorite_id, lock=True))


@router.post('/{favorite_id}/refresh', dependencies=[Depends(write_guard)])
def refresh(favorite_id: str, request: Request, user=Depends(current_user)):
    from .web import SEARCHERS
    with store(request).session() as db:
        saved = record(owned(db, user['id'], favorite_id))['item']
    match = None
    try:
        with SEARCHERS[Game(saved['game'])]() as searcher:
            for page in searcher.iter_pages(saved['card_number']):
                match = next((item for item in page.items if item.product_url == saved['product_url']), None)
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
