"""Saved graded card certificates; each row keeps the latest official lookup.

Lookups go through ``grading.lookup_certificate`` and can take minutes, so they always
run between two short sessions: the pool has four connections and none is held while
the provider answers. A lookup sent with a ``request_id`` can be cancelled through
``DELETE /lookups/{request_id}``; a cancelled lookup is never saved.
"""
import json
import re
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth.dependencies import current_user, write_guard
from .database import is_unique_violation
from .db_models import GradedCard
from .grading import LOOKUPS, lookup_certificate, normalize_request, present

router = APIRouter(prefix='/api/graded-cards')


REQUEST_ID = r'^[A-Za-z0-9-]{8,64}$'


class LookupInput(BaseModel):
    # Chosen by the browser so it can cancel the lookup before this request answers.
    request_id: str | None = Field(default=None, pattern=REQUEST_ID)


class GradedCardInput(LookupInput):
    provider: StrictStr
    cert_number: StrictStr


def keep_result(token):
    if not token.finish():
        raise HTTPException(409, detail='grading_lookup_cancelled')


def store(request: Request):
    return request.app.state.auth.store


def snapshot(result):
    return json.dumps({key: result[key] for key in ('source_url', 'certificate', 'images')}, ensure_ascii=False)


def record(row: GradedCard):
    data = json.loads(row.snapshot)
    certificate = data.get('certificate') or {}
    title, fields, english = present(row.provider, certificate)
    return {'id': row.id, 'provider': row.provider, 'cert_number': row.cert_number,
            'source_url': data.get('source_url'), 'title': title,
            'grade': certificate.get('grade') or '', 'fields': fields, 'english_fields': english,
            'population': certificate.get('population') or {}, 'images': data.get('images') or [],
            'created_at': row.created_at, 'refreshed_at': row.refreshed_at}


def owned(db: Session, user_id, card_id, *, lock=False):
    query = select(GradedCard).where(GradedCard.user_id == user_id, GradedCard.id == card_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, detail='graded_card_not_found')
    return row


def reject_duplicate(db: Session, user_id, provider, cert_number):
    card_id = db.scalar(select(GradedCard.id).where(
        GradedCard.user_id == user_id, GradedCard.provider == provider, GradedCard.cert_number == cert_number))
    if card_id:
        raise HTTPException(409, detail={'type': 'graded_card_duplicate', 'id': card_id})


@router.get('')
def graded_cards(request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        rows = db.scalars(select(GradedCard).where(GradedCard.user_id == user['id']).order_by(
            GradedCard.created_at.desc(), GradedCard.sort_id.desc()))
        return {'items': [record(row) for row in rows]}


@router.post('', status_code=201, dependencies=[Depends(write_guard)])
def add(body: GradedCardInput, request: Request, user=Depends(current_user)):
    provider, cert_number = normalize_request(body.provider, body.cert_number)
    # A card already saved answers at once instead of after a slow lookup.
    with store(request).session() as db:
        reject_duplicate(db, user['id'], provider.id, cert_number)
    with LOOKUPS.track(user['id'], body.request_id) as token:
        result = lookup_certificate(provider.id, cert_number, user['id'])
        keep_result(token)
        now = int(time.time())
        with store(request).session() as db:
            row = GradedCard(id=str(uuid.uuid4()), user_id=user['id'], provider=provider.id,
                             cert_number=cert_number, snapshot=snapshot(result), created_at=now, refreshed_at=now)
            try:
                with db.begin_nested():
                    db.add(row)
                    db.flush()
            except IntegrityError as exc:
                # Another request saved the same certificate while this one was looking it up.
                if is_unique_violation(exc, 'graded_cards_user_cert_key'):
                    reject_duplicate(db, user['id'], provider.id, cert_number)
                raise
            return record(row)


@router.post('/{card_id}/refresh', dependencies=[Depends(write_guard)])
def refresh(card_id: str, request: Request, body: LookupInput, user=Depends(current_user)):
    with store(request).session() as db:
        row = owned(db, user['id'], card_id)
        provider, cert_number = row.provider, row.cert_number
    # A failed or cancelled lookup raises before the write, so the saved snapshot stays.
    with LOOKUPS.track(user['id'], body.request_id) as token:
        result = lookup_certificate(provider, cert_number, user['id'])
        keep_result(token)
        with store(request).session() as db:
            row = owned(db, user['id'], card_id, lock=True)
            row.snapshot, row.refreshed_at = snapshot(result), int(time.time())
            return record(row)


@router.delete('/lookups/{request_id}', status_code=204, dependencies=[Depends(write_guard)])
def cancel_lookup(request_id: str, user=Depends(current_user)):
    """Stops this account's lookup; 409 when it already finished and was saved."""
    if not re.fullmatch(REQUEST_ID, request_id):
        raise HTTPException(422, detail='invalid_request_id')
    if LOOKUPS.cancel(user['id'], request_id) == 'finished':
        raise HTTPException(409, detail='graded_lookup_finished')


@router.delete('/{card_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove(card_id: str, request: Request, user=Depends(current_user)):
    with store(request).session() as db:
        db.delete(owned(db, user['id'], card_id, lock=True))
