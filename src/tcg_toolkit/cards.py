"""Private, manually maintained card inventory."""
from psycopg.errors import UniqueViolation
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


def record(row):
    return {key: row[key] for key in ('id', 'game', 'card_number', 'rarity', 'name',
                                    'quantity', 'notes', 'image_url', 'created_at', 'updated_at')}


def owned(db, user_id, card_id):
    row = db.execute('SELECT * FROM owned_cards WHERE user_id = %s AND id = %s',
                     (user_id, card_id)).fetchone()
    if row is None:
        raise HTTPException(404, detail='card_not_found')
    return row


def values(body):
    return (body.game, body.card_number, body.rarity, body.name, body.quantity,
            body.notes, body.game.casefold(), body.card_number.casefold(), body.rarity.casefold())


def conflict(db, user_id, body):
    row = db.execute('''SELECT id FROM owned_cards WHERE user_id = %s
        AND game_key = %s AND number_key = %s AND rarity_key = %s''',
        (user_id, body.game.casefold(), body.card_number.casefold(), body.rarity.casefold())).fetchone()
    if row:
        raise HTTPException(409, detail={'type': 'card_duplicate', 'id': row['id']})


@router.get('')
def listing(request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.connect() as db:
        return {'items': [record(row) for row in db.execute(
            'SELECT * FROM owned_cards WHERE user_id = %s ORDER BY created_at DESC, sort_id DESC', (user['id'],))]}


@router.post('', status_code=201, dependencies=[Depends(write_guard)])
def add(body: CardInput, request: Request, user=Depends(current_user)):
    card_id, now = str(uuid.uuid4()), int(time.time())
    with request.app.state.auth.store.connect() as db:
        try:
            with db.transaction():
                db.execute('INSERT INTO owned_cards (id,user_id,game,card_number,rarity,name,quantity,notes,game_key,number_key,rarity_key,created_at,updated_at,image_url) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                       (card_id, user['id'], *values(body), now, now, body.image_url))
        except UniqueViolation:
            conflict(db, user['id'], body)
            raise
        return record(owned(db, user['id'], card_id))


@router.put('/{card_id}', dependencies=[Depends(write_guard)])
def update(card_id: str, body: CardInput, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.connect() as db:
        owned(db, user['id'], card_id)
        try:
            with db.transaction():
                db.execute('''UPDATE owned_cards SET game=%s, card_number=%s, rarity=%s, name=%s, quantity=%s,
                notes=%s, game_key=%s, number_key=%s, rarity_key=%s, image_url=%s, updated_at=%s WHERE user_id=%s AND id=%s''',
                (*values(body), body.image_url, int(time.time()), user['id'], card_id))
        except UniqueViolation:
            conflict(db, user['id'], body)
            raise
        return record(owned(db, user['id'], card_id))


@router.delete('/{card_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove(card_id: str, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.connect() as db:
        owned(db, user['id'], card_id)
        db.execute('DELETE FROM owned_cards WHERE user_id = %s AND id = %s', (user['id'], card_id))
