"""Durable, user-scoped reservations for non-repeatable cart writes."""
from hashlib import sha256
import json
import re
import time

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from .db_models import CartOperation

from fastapi import HTTPException
from fastapi.responses import JSONResponse


def reserve(store, user_id, key, parameters):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key):
        raise HTTPException(422, detail='invalid_idempotency_key')
    digest = sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
    with store.session() as db:
        now = int(time.time())
        inserted = db.scalar(insert(CartOperation).values(
            user_id=user_id, operation_key=key, request_hash=digest,
            state='processing', created_at=now, updated_at=now
        ).on_conflict_do_nothing(index_elements=[CartOperation.user_id, CartOperation.operation_key])
          .returning(CartOperation.operation_key))
        if inserted:
            return None
        row = db.get(CartOperation, (user_id, key))
        if row:
            if row.request_hash != digest:
                raise HTTPException(409, detail={'type': 'idempotency_key_conflict'})
            if row.state == 'processing':
                # Includes a process crash before the final result was saved.
                # Expiring this reservation could repeat an upstream mutation.
                raise HTTPException(409, detail={'type': 'cart_operation_pending', 'outcome': 'unknown'})
            return JSONResponse(json.loads(row.response_json), status_code=row.status_code,
                                headers={'Idempotency-Replayed': 'true'})


def finish(store, user_id, key, status_code, result):
    with store.session() as db:
        db.execute(update(CartOperation).where(
            CartOperation.user_id == user_id, CartOperation.operation_key == key
        ).values(state='complete', status_code=status_code, response_json=json.dumps(result), updated_at=int(time.time())))
