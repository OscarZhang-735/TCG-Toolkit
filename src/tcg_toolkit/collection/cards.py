"""Private card inventory HTTP endpoints and account-scoped mutations."""
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..auth.dependencies import current_user, require_trusted_origin, write_guard
from ..persistence.database import is_unique_violation
from ..persistence.models import CardGroup, OwnedCard
from . import transfer
from .group_routes import GroupTarget, group_router
from .groups import CollectionGroups, lock_user
from .purchases import PurchaseBatch, record_purchases
from .schemas import CardCreate, card_record, card_values

router = APIRouter(prefix='/api/cards')
collection_groups = CollectionGroups(CardGroup, OwnedCard, 'card_group')
router.include_router(group_router(collection_groups))


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


@router.get('')
def listing(request: Request, group_id: str | None = None, user=Depends(current_user)):
    with request.app.state.database.session() as db:
        query = select(OwnedCard).where(OwnedCard.user_id == user['id'])
        if group_id is not None:
            collection_groups.owned(db, user['id'], group_id)
            query = query.where(OwnedCard.group_id == group_id)
        rows = db.scalars(query.order_by(OwnedCard.created_at.desc(), OwnedCard.sort_id.desc()))
        return {'items': [card_record(row) for row in rows]}


@router.get('/export')
def export_cards(request: Request, format: Literal['xlsx', 'json'],
                 language: Literal['en', 'zh-CN'] | None = None,
                 fields: Annotated[list[Literal[transfer.EXPORT_COLUMNS]] | None, Query()] = None,
                 games: Annotated[list[str] | None, Query()] = None,
                 group_id: str | None = None,
                 user=Depends(current_user)):
    content, media_type = transfer.export_card_rows(request.app.state.database, user['id'], format,
        language=language, fields=fields, games=games, group_id=group_id)
    return Response(content, media_type=media_type, headers={
        'Content-Disposition': f'attachment; filename="cards.{format}"', 'Cache-Control': 'no-store'})


@router.post('/import', dependencies=[Depends(require_trusted_origin)])
async def import_cards(request: Request, format: Literal['xlsx', 'json'], user=Depends(current_user)):
    media_type = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if media_type != ('application/json' if format == 'json' else transfer.XLSX_MEDIA_TYPE):
        raise HTTPException(415, detail='cards_import_format')
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > transfer.IMPORT_LIMIT:
            raise HTTPException(413, detail='cards_import_too_large')
        content.extend(chunk)
    return await run_in_threadpool(transfer.import_card_rows, content, format, request.app.state.database, user['id'])


@router.post('', status_code=201, dependencies=[Depends(write_guard)])
def add(body: CardCreate, request: Request, user=Depends(current_user)):
    now = int(time.time())
    with request.app.state.database.session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else collection_groups.owned(db, user['id'], body.group_id).id
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


@router.post('/purchases', dependencies=[Depends(write_guard)])
def add_purchases(body: PurchaseBatch, request: Request,
                  key: Annotated[str, Header(alias='Idempotency-Key', pattern=r'^[A-Za-z0-9_.:-]{1,128}$')],
                  user=Depends(current_user)):
    return record_purchases(request.app.state.database, collection_groups, user['id'], key, body)


@router.put('/{card_id}/group', dependencies=[Depends(write_guard)])
def move(card_id: str, body: GroupTarget, request: Request, user=Depends(current_user)):
    with request.app.state.database.session() as db:
        lock_user(db, user['id'])
        group_id = None if body.group_id is None else collection_groups.owned(db, user['id'], body.group_id).id
        row = owned(db, user['id'], card_id, lock=True)
        row.group_id = group_id
        return card_record(row)


@router.put('/{card_id}', dependencies=[Depends(write_guard)])
def update(card_id: str, body: CardCreate, request: Request, user=Depends(current_user)):
    with request.app.state.database.session() as db:
        # group_id is optional here: sent, it moves the card in the same write (null ungroups it);
        # omitted, the card stays where it is. The user lock orders this against group deletion.
        moving = 'group_id' in body.model_fields_set
        if moving:
            lock_user(db, user['id'])
            group_id = None if body.group_id is None else collection_groups.owned(db, user['id'], body.group_id).id
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
    with request.app.state.database.session() as db:
        db.delete(owned(db, user['id'], card_id, lock=True))
