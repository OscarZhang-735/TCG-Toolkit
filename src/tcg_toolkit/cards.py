"""Private, manually maintained card inventory."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .database import is_unique_violation
from .db_models import OwnedCard
import time
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .auth.routes import current_user, write_guard

router = APIRouter(prefix='/api/cards')
GAMES = {'ws': 'WS', 'ws_en': 'WS EN', 'ws en': 'WS EN', 'wsr': 'WSR', 'pm': 'PM', 'sev': 'SEV', 'sev_en': 'SEV EN', 'sev en': 'SEV EN', 'lycee': 'Lycee',
         'chaos': 'ChaosTCG', 'chaostcg': 'ChaosTCG'}


class CardInput(BaseModel):
    game: str = Field(min_length=1)
    card_number: str = Field(min_length=1)
    rarity: str = Field(min_length=1)
    name: str = Field(min_length=1)
    quantity: int = Field(default=1, strict=True, ge=0, le=9007199254740991)
    notes: str = ''
    image_url: str = ''

    @field_validator('image_url', mode='before')
    @classmethod
    def image_location(cls, value):
        if value is None:
            return ''
        if not isinstance(value, str):
            raise ValueError('Invalid image URL')
        value = value.strip()
        if value:
            url = urlsplit(value)
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
                raise ValueError('Image URL must be HTTP or HTTPS')
        return value


    @field_validator('game', 'card_number', 'rarity', 'name', 'notes')
    @classmethod
    def clean(cls, value, info):
        value = value.strip()
        if info.field_name != 'notes' and not value:
            raise ValueError('Must not be blank')
        return GAMES.get(value.casefold(), value) if info.field_name == 'game' else value


def record(row: OwnedCard):
    return {key: getattr(row, key) for key in ('id', 'game', 'card_number', 'rarity', 'name',
                                              'quantity', 'notes', 'image_url', 'created_at', 'updated_at')}


def owned(db: Session, user_id, card_id, *, lock=False):
    query = select(OwnedCard).where(OwnedCard.user_id == user_id, OwnedCard.id == card_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail='card_not_found')
    return row


def values(body):
    return dict(game=body.game, card_number=body.card_number, rarity=body.rarity, name=body.name,
                quantity=body.quantity, notes=body.notes, image_url=body.image_url,
                game_key=body.game.casefold(), number_key=body.card_number.casefold(), rarity_key=body.rarity.casefold())


def conflict(db: Session, user_id, body):
    card_id = db.scalar(select(OwnedCard.id).where(
        OwnedCard.user_id == user_id, OwnedCard.game_key == body.game.casefold(),
        OwnedCard.number_key == body.card_number.casefold(), OwnedCard.rarity_key == body.rarity.casefold()))
    if card_id:
        raise HTTPException(409, detail={'type': 'card_duplicate', 'id': card_id})


@router.get('')
def listing(request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        rows = db.scalars(select(OwnedCard).where(OwnedCard.user_id == user['id']).order_by(
            OwnedCard.created_at.desc(), OwnedCard.sort_id.desc()))
        return {'items': [record(row) for row in rows]}


@router.post('', status_code=201, dependencies=[Depends(write_guard)])
def add(body: CardInput, request: Request, user=Depends(current_user)):
    now = int(time.time())
    with request.app.state.auth.store.session() as db:
        row = OwnedCard(id=str(uuid.uuid4()), user_id=user['id'], created_at=now, updated_at=now, **values(body))
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            if is_unique_violation(exc):
                conflict(db, user['id'], body)
            raise
        return record(row)


@router.put('/{card_id}', dependencies=[Depends(write_guard)])
def update(card_id: str, body: CardInput, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        row = owned(db, user['id'], card_id, lock=True)
        try:
            with db.begin_nested():
                for key, value in values(body).items():
                    setattr(row, key, value)
                row.updated_at = int(time.time())
                db.flush()
        except IntegrityError as exc:
            if is_unique_violation(exc):
                conflict(db, user['id'], body)
            raise
        return record(row)


@router.delete('/{card_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove(card_id: str, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        db.delete(owned(db, user['id'], card_id, lock=True))
