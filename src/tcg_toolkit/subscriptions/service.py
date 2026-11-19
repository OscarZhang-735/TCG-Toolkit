"""Manage an account's subscriptions and query interval."""
import json
import time
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from ..catalog.providers.yuyutei_products import fetch_product_snapshot
from ..persistence.models import Subscription, User
from .recipients import notification_recipient
from .schedule import scheduled_scan


class VerifiedEmailRequired(Exception):
    """The account needs a verified address for subscription notifications."""


def _record(row):
    return dict(id=row.id, item=json.loads(row.snapshot), created_at=row.created_at,
                checked_at=row.checked_at, last_error=row.last_error,
                next_check_at=row.next_check_at)


def _settings(db, account):
    """The account interval and the scan expected to check its earliest due card."""
    due = db.scalar(select(func.min(Subscription.next_check_at)).where(Subscription.user_id == account.id))
    return {'interval_hours': account.subscription_interval_hours,
            'next_check_at': None if due is None else scheduled_scan(due, time.time())}


def get_settings(database, user_id):
    with database.session() as db:
        return _settings(db, db.get(User, user_id))


def update_interval(database, user_id, interval_hours):
    with database.session() as db:
        account = db.get(User, user_id, with_for_update=True)
        account.subscription_interval_hours = interval_hours
        db.execute(update(Subscription).where(Subscription.user_id == user_id).values(
            next_check_at=Subscription.checked_at + interval_hours * 3600))
        return _settings(db, account)


def list_subscriptions(database, user_id):
    with database.session() as db:
        rows = db.scalars(select(Subscription).where(Subscription.user_id == user_id)
                          .order_by(Subscription.created_at.desc(), Subscription.id))
        return [_record(row) for row in rows]


def subscribe(database, user_id, game, product_url):
    query = select(Subscription).where(Subscription.user_id == user_id, Subscription.game == game.value,
                                       Subscription.product_url == product_url)
    with database.session() as db:
        if not notification_recipient(db.get(User, user_id)):
            raise VerifiedEmailRequired
        row = db.scalar(query)
        if row is not None:
            return _record(row)
    now = int(time.time())
    snapshot = fetch_product_snapshot(game, product_url)
    with database.session() as db:
        # Serialize creation with account-wide interval changes after network I/O.
        account = db.get(User, user_id, with_for_update=True)
        db.execute(insert(Subscription).values(
            id=str(uuid.uuid4()), user_id=user_id, game=game.value,
            product_url=product_url, snapshot=json.dumps(snapshot),
            created_at=now, checked_at=now,
            next_check_at=now + account.subscription_interval_hours * 3600,
        ).on_conflict_do_nothing(index_elements=['user_id', 'game', 'product_url']))
        return _record(db.scalar(query))


def unsubscribe(database, user_id, subscription_id):
    with database.session() as db:
        row = db.scalar(select(Subscription).where(Subscription.id == subscription_id,
                        Subscription.user_id == user_id).with_for_update())
        if row is None:
            return False
        db.delete(row)
        return True
