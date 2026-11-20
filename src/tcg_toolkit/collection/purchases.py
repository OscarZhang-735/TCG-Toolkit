"""Apply a confirmed purchase list to the user's card inventory."""
from hashlib import sha256
import json
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert

from ..persistence.models import CartOperation, OwnedCard
from .groups import lock_user
from .schemas import CardInput, MAX_CARD_QUANTITY, card_values


class PurchasedCard(CardInput):
    quantity: int = Field(strict=True, gt=0, le=MAX_CARD_QUANTITY)


class PurchaseBatch(BaseModel):
    items: list[PurchasedCard] = Field(min_length=1)
    group_id: str | None = Field(default=None, min_length=1)


def increment_cards(db, user_id, group_id, items, now):
    cards = {}
    for item in items:
        fields = card_values(item)
        key = (fields['game_key'], fields['number_key'], fields['rarity_key'])
        if key not in cards:
            cards[key] = dict(**fields, id=str(uuid.uuid4()), user_id=user_id, group_id=group_id,
                              created_at=now, updated_at=now)
        else:
            cards[key]['quantity'] += item.quantity
            if cards[key]['quantity'] > MAX_CARD_QUANTITY:
                raise HTTPException(422, detail='card_quantity_overflow')

    statement = insert(OwnedCard)
    purchased = statement.excluded.quantity
    upsert = statement.on_conflict_do_update(
        index_elements=['user_id', 'game_key', 'number_key', 'rarity_key'],
        set_={'quantity': OwnedCard.quantity + purchased, 'updated_at': now},
        where=OwnedCard.quantity <= MAX_CARD_QUANTITY - purchased,
    ).returning(OwnedCard.id)
    saved = set(db.scalars(upsert, list(cards.values())))
    if len(saved) != len(cards):
        raise HTTPException(422, detail='card_quantity_overflow')
    created = sum(card['id'] in saved for card in cards.values())
    return {'created': created, 'updated': len(saved) - created, 'quantity': sum(card['quantity'] for card in cards.values())}


def record_purchases(store, groups, user_id, key, body):
    # '/' cannot appear in an external cart Idempotency-Key. Store this receipt
    # in the existing ledger in the same transaction as the inventory changes.
    receipt_key = 'card-purchases/' + key
    digest = sha256(body.model_dump_json().encode()).hexdigest()
    now = int(time.time())
    with store.session() as db:
        lock_user(db, user_id)
        prior = db.get(CartOperation, (user_id, receipt_key))
        if prior:
            if prior.request_hash != digest:
                raise HTTPException(409, detail='idempotency_key_conflict')
            return json.loads(prior.response_json)
        if body.group_id is not None:
            groups.owned(db, user_id, body.group_id)
        result = increment_cards(db, user_id, body.group_id, body.items, now)
        db.add(CartOperation(user_id=user_id, operation_key=receipt_key, request_hash=digest, state='complete',
                             response_json=json.dumps(result), status_code=200, created_at=now, updated_at=now))
        return result
