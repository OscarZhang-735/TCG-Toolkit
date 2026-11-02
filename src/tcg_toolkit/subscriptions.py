"""Account-scoped subscriptions to normal-condition YuYu-Tei offers."""
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .auth.routes import current_user, write_guard
from .db_models import Subscription, User
from .notification_email import notification_recipient
from .models import Game
from .product_details import fetch_product_snapshot, normalize_product_url

router = APIRouter(prefix='/api/subscriptions')


class SubscribeInput(BaseModel):
    game: Game
    product_url: str = Field(max_length=512)

    @model_validator(mode='after')
    def product_location(self):
        self.product_url = normalize_product_url(self.game, self.product_url)
        return self


def record(row):
    return dict(id=row.id, item=json.loads(row.snapshot), created_at=row.created_at,
                checked_at=row.checked_at, last_error=row.last_error)


@router.get('')
def listing(request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        rows = db.scalars(select(Subscription).where(Subscription.user_id == user['id'])
                          .order_by(Subscription.created_at.desc(), Subscription.id))
        return {'items': [record(row) for row in rows]}


@router.post('', dependencies=[Depends(write_guard)])
def add(body: SubscribeInput, request: Request, user=Depends(current_user)):
    store = request.app.state.auth.store
    with store.session() as db:
        if not notification_recipient(db.get(User, user['id'])):
            raise HTTPException(403, detail='verified_email_required')
    query = select(Subscription).where(Subscription.user_id == user['id'],
                                      Subscription.game == body.game.value,
                                      Subscription.product_url == body.product_url)
    with store.session() as db:
        row = db.scalar(query)
        if row is not None:
            return record(row)
    snapshot = fetch_product_snapshot(body.game, body.product_url)
    now = int(time.time())
    with store.session() as db:
        db.execute(insert(Subscription).values(
            id=str(uuid.uuid4()), user_id=user['id'], game=body.game.value,
            product_url=body.product_url, snapshot=json.dumps(snapshot),
            created_at=now, checked_at=now,
        ).on_conflict_do_nothing(index_elements=['user_id', 'game', 'product_url']))
        return record(db.scalar(query))


@router.delete('/{subscription_id}', status_code=204, dependencies=[Depends(write_guard)])
def remove(subscription_id: str, request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        row = db.scalar(select(Subscription).where(Subscription.id == subscription_id,
                        Subscription.user_id == user['id']).with_for_update())
        if row is None:
            raise HTTPException(404, detail='subscription_not_found')
        db.delete(row)
