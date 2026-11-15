"""Private card inventory HTTP endpoints and account-scoped mutations."""
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select, update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import card_transfer
from .auth.dependencies import current_user, require_trusted_origin, write_guard
from .card_schemas import CardCreate, GroupName, GroupTarget, card_record, card_values
from .database import is_unique_violation
from .db_models import CardGroup, OwnedCard, User

router = APIRouter(prefix='/api/cards')


def owned(db: Session, user_id, card_id, *, lock=False):
    query = select(OwnedCard).where(OwnedCard.user_id == user_id, OwnedCard.id == card_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail='card_not_found')
    return row


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
        return {'items': [card_record(row) for row in rows]}


@router.get('/export')
def export_cards(request: Request, format: Literal['xlsx', 'json'],
                 language: Literal['en', 'zh-CN'] | None = None,
                 fields: Annotated[list[Literal[card_transfer.EXPORT_COLUMNS]] | None, Query()] = None,
                 games: Annotated[list[str] | None, Query()] = None,
                 group_id: str | None = None,
                 user=Depends(current_user)):
    content, media_type = card_transfer.export_card_rows(request.app.state.auth.store, user['id'], format,
        language=language, fields=fields, games=games, group_id=group_id)
    return Response(content, media_type=media_type, headers={
        'Content-Disposition': f'attachment; filename="cards.{format}"', 'Cache-Control': 'no-store'})


@router.post('/import', dependencies=[Depends(require_trusted_origin)])
async def import_cards(request: Request, format: Literal['xlsx', 'json'], user=Depends(current_user)):
    media_type = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if media_type != ('application/json' if format == 'json' else card_transfer.XLSX_MEDIA_TYPE):
        raise HTTPException(415, detail='cards_import_format')
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > card_transfer.IMPORT_LIMIT:
            raise HTTPException(413, detail='cards_import_too_large')
        content.extend(chunk)
    return await run_in_threadpool(card_transfer.import_card_rows, content, format, request.app.state.auth.store, user['id'])


@router.post('', status_code=201, dependencies=[Depends(write_guard)])
def add(body: CardCreate, request: Request, user=Depends(current_user)):
    now = int(time.time())
    with request.app.state.auth.store.session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else owned_group(db, user['id'], body.group_id).id
        row = OwnedCard(id=str(uuid.uuid4()), user_id=user['id'], group_id=group_id,
                        created_at=now, updated_at=now, **card_values(body))
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            if is_unique_violation(exc):
                conflict(db, user['id'], body)
            raise
        return card_record(row)


@router.put('/{card_id}/group', dependencies=[Depends(write_guard)])
def move(card_id: str, body: GroupTarget, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else owned_group(db, user['id'], body.group_id).id
        row = owned(db, user['id'], card_id, lock=True)
        row.group_id = group_id
        return card_record(row)


@router.put('/{card_id}', dependencies=[Depends(write_guard)])
def update(card_id: str, body: CardCreate, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        # group_id is optional here: sent, it moves the card in the same write (null ungroups it);
        # omitted, the card stays where it is. The user lock orders this against group deletion.
        moving = 'group_id' in body.model_fields_set
        if moving:
            lock_user(db, user['id'])
            group_id = None if body.group_id is None else owned_group(db, user['id'], body.group_id).id
        row = owned(db, user['id'], card_id, lock=True)
        if moving:
            row.group_id = group_id
        try:
            with db.begin_nested():
                for key, value in card_values(body).items():
                    setattr(row, key, value)
                row.updated_at = int(time.time())
                db.flush()
        except IntegrityError as exc:
            if is_unique_violation(exc):
                conflict(db, user['id'], body)
            raise
        return card_record(row)


@router.delete('/{card_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove(card_id: str, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        db.delete(owned(db, user['id'], card_id, lock=True))
