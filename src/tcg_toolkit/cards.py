"""Private, manually maintained card inventory."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .database import is_unique_violation
from .db_models import OwnedCard
import time
import uuid
import json
from io import BytesIO
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from xlsxwriter import Workbook
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


EXPORT_COLUMNS = ('game', 'card_number', 'rarity', 'name', 'quantity', 'notes',
                  'image_url', 'id', 'created_at', 'updated_at')
EXPORT_WIDTHS = {'game': 20, 'card_number': 20, 'rarity': 20, 'name': 36, 'quantity': 18,
                 'notes': 40, 'image_url': 40, 'id': 40, 'created_at': 20, 'updated_at': 20}
# XLSX headings are for people; JSON keys stay stable so files remain machine-readable.
EXPORT_HEADINGS = {
    'en': {'sheet': 'Cards', 'game': 'Game', 'card_number': 'Number', 'rarity': 'Rarity', 'name': 'Name',
           'quantity': 'Quantity', 'notes': 'Notes', 'image_url': 'Image URL', 'id': 'ID',
           'created_at': 'Created at', 'updated_at': 'Updated at'},
    'zh-CN': {'sheet': '卡牌', 'game': '游戏', 'card_number': '编号', 'rarity': '稀有度', 'name': '名称',
              'quantity': '拥有量', 'notes': '备注', 'image_url': '图片地址', 'id': 'ID',
              'created_at': '创建时间', 'updated_at': '更新时间'},
}


@router.get('/export')
def export_cards(request: Request, format: Literal['xlsx', 'json'],
                 language: Literal['en', 'zh-CN'] | None = None,
                 fields: Annotated[list[Literal[EXPORT_COLUMNS]] | None, Query()] = None,
                 games: Annotated[list[str] | None, Query()] = None,
                 user=Depends(current_user)):
    items = listing(request, user)['items']
    if games is not None:
        wanted = {game.strip().casefold() for game in games}
        items = [item for item in items if item['game'].casefold() in wanted]
    # Canonical column order, regardless of the order fields were requested in.
    columns = [key for key in EXPORT_COLUMNS if fields is None or key in fields]
    items = [{key: item[key] for key in columns} for item in items]
    headers = {'Content-Disposition': f'attachment; filename="cards.{format}"',
               'Cache-Control': 'no-store'}
    if format == 'json':
        return Response(json.dumps({'items': items}, ensure_ascii=False, indent=2),
                        media_type='application/json', headers=headers)
    # Without a language, headings are the raw keys (stable for scripts and re-import).
    labels = EXPORT_HEADINGS.get(language) or {'sheet': 'Cards', **{key: key for key in EXPORT_COLUMNS}}
    output = BytesIO()
    with Workbook(output, {'in_memory': True}) as workbook:
        sheet = workbook.add_worksheet(labels['sheet'])
        bold = workbook.add_format({'bold': True, 'bg_color': '#E8EEF7'})
        sheet.write_row(0, 0, [labels[key] for key in columns], bold)
        sheet.freeze_panes(1, 0)
        for column_index, key in enumerate(columns):
            sheet.set_column(column_index, column_index, EXPORT_WIDTHS[key])
        for row_index, item in enumerate(items, 1):
            for column_index, key in enumerate(columns):
                value = item[key]
                # Excel preserves only 15 numeric digits; retain larger quantities as text.
                if isinstance(value, int) and abs(value) < 10**15:
                    sheet.write_number(row_index, column_index, value)
                else:
                    # Explicit strings preserve card numbers and never execute formulas.
                    result = sheet.write_string(row_index, column_index, str(value))
                    if result == -2:
                        raise HTTPException(422, detail='export_xlsx_text_too_long')
        sheet.autofilter(0, 0, len(items), len(columns) - 1)
    return Response(output.getvalue(),
                    media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers=headers)


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
