"""Durable, user-scoped reservations for non-repeatable cart writes."""
from hashlib import sha256
import json
import re
import time

from fastapi import HTTPException
from fastapi.responses import JSONResponse


def reserve(store, user_id, key, parameters):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key):
        raise HTTPException(422, detail='invalid_idempotency_key')
    digest = sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
    with store.connect() as db:
        now = int(time.time())
        inserted = db.execute('''INSERT INTO cart_operations
            (user_id,operation_key,request_hash,state,status_code,response_json,created_at,updated_at)
            VALUES (%s,%s,%s,'processing',NULL,NULL,%s,%s)
            ON CONFLICT(user_id,operation_key) DO NOTHING RETURNING operation_key''',
            (user_id, key, digest, now, now)).fetchone()
        if inserted:
            return None
        row = db.execute('SELECT * FROM cart_operations WHERE user_id=%s AND operation_key=%s',
                         (user_id, key)).fetchone()
        if row:
            if row['request_hash'] != digest:
                raise HTTPException(409, detail={'type': 'idempotency_key_conflict'})
            if row['state'] == 'processing':
                # Includes a process crash before the final result was saved.
                # Expiring this reservation could repeat an upstream mutation.
                raise HTTPException(409, detail={'type': 'cart_operation_pending', 'outcome': 'unknown'})
            return JSONResponse(json.loads(row['response_json']), status_code=row['status_code'],
                                headers={'Idempotency-Replayed': 'true'})


def finish(store, user_id, key, status_code, result):
    with store.connect() as db:
        db.execute('''UPDATE cart_operations SET state='complete', status_code=%s, response_json=%s,
            updated_at=%s WHERE user_id=%s AND operation_key=%s''',
                   (status_code, json.dumps(result), int(time.time()), user_id, key))
