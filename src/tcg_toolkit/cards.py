"""Private, manually maintained card inventory."""
from sqlalchemy import select, update as sql_update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .database import is_unique_violation
from .db_models import CardGroup, OwnedCard, User
import time
import uuid
import json
from decimal import Decimal
from io import BytesIO
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from defusedxml.common import DefusedXmlException
from starlette.concurrency import run_in_threadpool
from xlsxwriter import Workbook
from pydantic import BaseModel, Field, ValidationError, field_validator

from .auth.routes import current_user, require_trusted_origin, write_guard

router = APIRouter(prefix='/api/cards')
GAMES = {'ws': 'WS', 'ws_en': 'WS EN', 'ws en': 'WS EN', 'wsr': 'WSR', 'pm': 'PM', 'sev': 'SEV', 'sev_en': 'SEV EN', 'sev en': 'SEV EN', 'lycee': 'Lycee',
         'chaos': 'ChaosTCG', 'chaostcg': 'ChaosTCG'}


class CardInput(BaseModel):
    game: str = Field(min_length=1)
    card_number: str = Field(min_length=1)
    rarity: str = Field(min_length=1)
    name: str = Field(min_length=1)
    quantity: int = Field(default=1, strict=True, ge=0, le=9007199254740991)
    price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
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


class CardCreate(CardInput):
    group_id: str | None = Field(default=None, min_length=1)


class GroupName(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class GroupTarget(BaseModel):
    group_id: str | None = Field(min_length=1)


def record(row: OwnedCard):
    return {**{key: getattr(row, key) for key in ('id', 'group_id', 'game', 'card_number', 'rarity', 'name',
                                                'quantity', 'notes', 'image_url', 'created_at', 'updated_at')},
            'price': float(row.price) if row.price is not None else None}


def owned(db: Session, user_id, card_id, *, lock=False):
    query = select(OwnedCard).where(OwnedCard.user_id == user_id, OwnedCard.id == card_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail='card_not_found')
    return row


def values(body):
    return dict(game=body.game, card_number=body.card_number, rarity=body.rarity, name=body.name,
                quantity=body.quantity, price=body.price, notes=body.notes, image_url=body.image_url,
                game_key=body.game.casefold(), number_key=body.card_number.casefold(), rarity_key=body.rarity.casefold())


def conflict(db: Session, user_id, body):
    card_id = db.scalar(select(OwnedCard.id).where(
        OwnedCard.user_id == user_id, OwnedCard.game_key == body.game.casefold(),
        OwnedCard.number_key == body.card_number.casefold(), OwnedCard.rarity_key == body.rarity.casefold()))
    if card_id:
        raise HTTPException(409, detail={'type': 'card_duplicate', 'id': card_id})


def lock_user(db: Session, user_id):
    # Serialize group deletion and membership writes before taking card locks.
    db.execute(select(User.id).where(User.id == user_id).with_for_update()).one()


def owned_group(db: Session, user_id, group_id):
    row = db.scalar(select(CardGroup).where(CardGroup.user_id == user_id, CardGroup.id == group_id))
    if row is None:
        raise HTTPException(404, detail='card_group_not_found')
    return row


def group_record(row: CardGroup):
    return {'id': row.id, 'name': row.name, 'created_at': row.created_at}


def require_available_name(db: Session, user_id, name, group_id=None):
    query = select(CardGroup.id).where(CardGroup.user_id == user_id, CardGroup.name == name)
    if group_id is not None:
        query = query.where(CardGroup.id != group_id)
    if db.scalar(query) is not None:
        raise HTTPException(409, detail='card_group_name_taken')


@router.get('/groups')
def groups(request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        rows = db.scalars(select(CardGroup).where(CardGroup.user_id == user['id']).order_by(
            CardGroup.created_at, CardGroup.id))
        return {'items': [group_record(row) for row in rows]}


@router.post('/groups', status_code=201, dependencies=[Depends(write_guard)])
def create_group(body: GroupName, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        lock_user(db, user['id'])
        require_available_name(db, user['id'], body.name)
        row = CardGroup(id=str(uuid.uuid4()), user_id=user['id'], name=body.name, created_at=int(time.time()))
        db.add(row)
        return group_record(row)


@router.put('/groups/{group_id}', dependencies=[Depends(write_guard)])
def rename_group(group_id: str, body: GroupName, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        lock_user(db, user['id'])
        row = owned_group(db, user['id'], group_id)
        require_available_name(db, user['id'], body.name, row.id)
        row.name = body.name
        return group_record(row)


@router.delete('/groups/{group_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove_group(group_id: str, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        lock_user(db, user['id'])
        row = owned_group(db, user['id'], group_id)
        db.execute(sql_update(OwnedCard).where(OwnedCard.user_id == user['id'], OwnedCard.group_id == row.id)
                   .values(group_id=None))
        db.delete(row)


@router.get('')
def listing(request: Request, group_id: str | None = None, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        query = select(OwnedCard).where(OwnedCard.user_id == user['id'])
        if group_id is not None:
            owned_group(db, user['id'], group_id)
            query = query.where(OwnedCard.group_id == group_id)
        rows = db.scalars(query.order_by(OwnedCard.created_at.desc(), OwnedCard.sort_id.desc()))
        return {'items': [record(row) for row in rows]}


# 'group' holds the group name (not its ID), so files carry groups between accounts.
EXPORT_COLUMNS = ('game', 'card_number', 'rarity', 'name', 'quantity', 'price', 'notes', 'group',
                  'image_url', 'id', 'created_at', 'updated_at')
XLSX_MEDIA_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
IMPORT_LIMIT = 10 * 1024 * 1024
REQUIRED_COLUMNS = {key for key, field in CardInput.model_fields.items() if field.is_required()}
IMPORT_COLUMNS = {*CardInput.model_fields, 'group'}
EXPORT_WIDTHS = {'game': 20, 'card_number': 20, 'rarity': 20, 'name': 36, 'quantity': 18, 'price': 18,
                 'notes': 40, 'group': 24, 'image_url': 40, 'id': 40, 'created_at': 20, 'updated_at': 20}
# XLSX headings are for people; JSON keys stay stable so files remain machine-readable.
EXPORT_HEADINGS = {
    'en': {'sheet': 'Cards', 'game': 'Game', 'card_number': 'Number', 'rarity': 'Rarity', 'name': 'Name',
           'quantity': 'Quantity', 'price': 'Price', 'notes': 'Notes', 'group': 'Group', 'image_url': 'Image URL', 'id': 'ID',
           'created_at': 'Created at', 'updated_at': 'Updated at'},
    'zh-CN': {'sheet': '卡牌', 'game': '游戏', 'card_number': '编号', 'rarity': '稀有度', 'name': '名称',
              'quantity': '拥有量', 'price': '价格', 'notes': '备注', 'group': '分组', 'image_url': '图片地址', 'id': 'ID',
              'created_at': '创建时间', 'updated_at': '更新时间'},
}

# Import reads raw keys and every exported heading language, so any export file round-trips.
HEADING_KEYS = {label.casefold(): key for labels in [{key: key for key in EXPORT_COLUMNS}, *EXPORT_HEADINGS.values()]
                for key, label in labels.items() if key != 'sheet'}


@router.get('/export')
def export_cards(request: Request, format: Literal['xlsx', 'json'],
                 language: Literal['en', 'zh-CN'] | None = None,
                 fields: Annotated[list[Literal[EXPORT_COLUMNS]] | None, Query()] = None,
                 games: Annotated[list[str] | None, Query()] = None,
                 group_id: str | None = None,
                 user=Depends(current_user)):
    items = listing(request, group_id=group_id, user=user)['items']
    names = {row['id']: row['name'] for row in groups(request, user=user)['items']}
    if games is not None:
        wanted = {game.strip().casefold() for game in games}
        items = [item for item in items if item['game'].casefold() in wanted]
    # Canonical column order, regardless of the order fields were requested in.
    columns = [key for key in EXPORT_COLUMNS if fields is None or key in fields]
    # Ungrouped cards export a null group (an empty XLSX cell).
    items = [{key: names.get(item['group_id']) if key == 'group' else item[key] for key in columns} for item in items]
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
                if value is None:
                    continue
                # Excel preserves only 15 numeric digits; retain larger quantities as text.
                if isinstance(value, (int, float)) and abs(value) < 10**15:
                    sheet.write_number(row_index, column_index, value)
                else:
                    # Explicit strings preserve card numbers and never execute formulas.
                    result = sheet.write_string(row_index, column_index, str(value))
                    if result == -2:
                        raise HTTPException(422, detail='export_xlsx_text_too_long')
        sheet.autofilter(0, 0, len(items), len(columns) - 1)
    return Response(output.getvalue(),
                    media_type=XLSX_MEDIA_TYPE,
                    headers=headers)


def xlsx_import_rows(content):
    with ZipFile(BytesIO(content)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 64 * 1024 * 1024:
            raise HTTPException(413, detail='cards_import_too_large')
    workbook = load_workbook(BytesIO(content), read_only=True, keep_links=False)
    try:
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()
        rows = sheet.iter_rows(max_col=len(EXPORT_COLUMNS) + 1)
        headings = [cell.value for cell in next(rows, ())]
        while headings and headings[-1] is None:
            headings.pop()
        headers = [HEADING_KEYS.get(value.strip().casefold()) if isinstance(value, str) else None
                   for value in headings]
        if (any(not isinstance(key, str) or key not in EXPORT_COLUMNS for key in headers)
                or len(set(headers)) != len(headers) or not REQUIRED_COLUMNS.issubset(headers)):
            raise HTTPException(422, detail='cards_import_headers')
        for line, cells in enumerate(rows, 2):
            if not any(cell.value is not None for cell in cells):
                continue
            if any(cell.value is not None for cell in cells[len(headers):]):
                raise HTTPException(422, detail={'code': 'cards_import_row', 'row': line})
            item = {}
            for key, cell in zip(headers, cells):
                if key not in IMPORT_COLUMNS or cell.value is None:
                    continue
                if cell.data_type in ('f', 'e'):
                    raise HTTPException(422, detail={'code': 'cards_import_row', 'row': line})
                value = cell.value
                if key == 'quantity' and isinstance(value, str) and value.isascii() and value.isdecimal():
                    value = int(value) if len(value) <= 16 else value
                if key in ('card_number', 'group') and type(value) is int:
                    value = str(value)
                item[key] = value
            yield line, item
    finally:
        workbook.close()


def import_group_name(value):
    """Group name from an import row; blank or missing means ungrouped."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Group must be text')
    value = value.strip()
    if len(value) > 100 or '\0' in value:
        raise ValueError('Invalid group name')
    value.encode('utf-8')
    return value or None


def assign_import_groups(db: Session, user_id, imported, wanted, now):
    """Put newly imported cards into groups by name, creating missing groups in file order."""
    members = {}
    for card_id, name in wanted:
        if name is not None and card_id in imported:
            members.setdefault(name, []).append(card_id)
    if not members:
        return
    existing = dict(db.execute(select(CardGroup.name, CardGroup.id).where(
        CardGroup.user_id == user_id, CardGroup.name.in_(list(members)))).all())
    missing = [name for name in members if name not in existing]
    # New groups share one timestamp; sorted IDs keep their (created_at, id) order matching the file.
    for name, group_id in zip(missing, sorted(str(uuid.uuid4()) for _ in missing)):
        db.add(CardGroup(id=group_id, user_id=user_id, name=name, created_at=now))
        existing[name] = group_id
    db.flush()
    for name, card_ids in members.items():
        db.execute(sql_update(OwnedCard).where(OwnedCard.user_id == user_id, OwnedCard.id.in_(card_ids))
                   .values(group_id=existing[name]))


def import_card_rows(content, format, store, user_id):
    now = int(time.time())
    try:
        if format == 'json':
            data = json.loads(content.decode('utf-8-sig'))
            items = data.get('items') if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise ValueError('Expected a list of cards')
            rows = enumerate(items, 1)
        else:
            rows = xlsx_import_rows(content)
        cards, wanted = [], []
        for line, item in rows:
            try:
                body = CardInput.model_validate(item)
                group = import_group_name(item.get('group'))
                fields = values(body)
                for value in fields.values():
                    if isinstance(value, str):
                        if '\0' in value:
                            raise ValueError('NUL character')
                        value.encode('utf-8')
            except (ValidationError, ValueError):
                raise HTTPException(422, detail={'code': 'cards_import_row', 'row': line}) from None
            card_id = str(uuid.uuid4())
            cards.append(dict(id=card_id, user_id=user_id, created_at=now, updated_at=now, **fields))
            wanted.append((card_id, group))
    except (BadZipFile, InvalidFileException, ParseError, DefusedXmlException, KeyError, IndexError,
            ValueError, TypeError, RecursionError, OSError):
        raise HTTPException(422, detail='cards_import_invalid') from None
    if not cards:
        return {'imported': 0, 'skipped': 0}
    statement = insert(OwnedCard).on_conflict_do_nothing(
        index_elements=['user_id', 'game_key', 'number_key', 'rarity_key']).returning(OwnedCard.id)
    # Skipped duplicates keep their existing group; only new cards join the file's groups.
    with store.session() as db:
        lock_user(db, user_id)
        imported = set(db.scalars(statement, cards).all())
        assign_import_groups(db, user_id, imported, wanted, now)
    return {'imported': len(imported), 'skipped': len(cards) - len(imported)}


@router.post('/import', dependencies=[Depends(require_trusted_origin)])
async def import_cards(request: Request, format: Literal['xlsx', 'json'], user=Depends(current_user)):
    media_type = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if media_type != ('application/json' if format == 'json' else XLSX_MEDIA_TYPE):
        raise HTTPException(415, detail='cards_import_format')
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > IMPORT_LIMIT:
            raise HTTPException(413, detail='cards_import_too_large')
        content.extend(chunk)
    return await run_in_threadpool(import_card_rows, content, format, request.app.state.auth.store, user['id'])


@router.post('', status_code=201, dependencies=[Depends(write_guard)])
def add(body: CardCreate, request: Request, user=Depends(current_user)):
    now = int(time.time())
    with request.app.state.auth.store.session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else owned_group(db, user['id'], body.group_id).id
        row = OwnedCard(id=str(uuid.uuid4()), user_id=user['id'], group_id=group_id,
                        created_at=now, updated_at=now, **values(body))
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            if is_unique_violation(exc):
                conflict(db, user['id'], body)
            raise
        return record(row)


@router.put('/{card_id}/group', dependencies=[Depends(write_guard)])
def move(card_id: str, body: GroupTarget, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else owned_group(db, user['id'], body.group_id).id
        row = owned(db, user['id'], card_id, lock=True)
        row.group_id = group_id
        return record(row)


@router.put('/{card_id}', dependencies=[Depends(write_guard)])
def update(card_id: str, body: CardInput, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        row = owned(db, user['id'], card_id, lock=True)
        try:
            with db.begin_nested():
                for key, value in values(body).items():
                    if key == 'price' and key not in body.model_fields_set:
                        continue
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
