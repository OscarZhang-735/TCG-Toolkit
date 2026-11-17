"""Atomic subscription checks, favorite synchronization and notification outbox."""
from collections import defaultdict
import json
import uuid

from sqlalchemy import func, select

from .db_models import Favorite, Subscription, SubscriptionEmail, User


CHANGE_FIELDS = ('price', 'stock_quantity', 'stock_status')


def apply_check_results(store, user_id, ids, products, *, run_id, local_date, started_at, slot, now):
    """Recheck due subscriptions under locks and commit each user's results together."""
    with store.session() as db:
        # Lock favorites before subscriptions, matching favorite removal.
        account = db.get(User, user_id, with_for_update=True)
        if account is None:
            return 0
        favorites = defaultdict(list)
        for favorite in db.scalars(select(Favorite).join(Subscription,
                (Favorite.user_id == Subscription.user_id)
                & (Favorite.game == Subscription.game)
                & (func.rtrim(Favorite.product_url, '/') == Subscription.product_url))
                .where(Favorite.user_id == user_id, Subscription.id.in_(ids))
                .order_by(Favorite.id).with_for_update(of=Favorite)):
            favorites[(favorite.game, favorite.product_url.rstrip('/'))].append(favorite)
        rows = db.scalars(select(Subscription).where(Subscription.id.in_(ids),
                          Subscription.next_check_at <= started_at)
                          .order_by(Subscription.id).with_for_update()).all()
        changes = []
        for row in rows:
            snapshot, error = products[(row.game, row.product_url)]
            row.checked_at, row.last_error = slot, error
            row.next_check_at = slot + account.subscription_interval_hours * 3600
            if error:
                continue
            before = json.loads(row.snapshot)
            if any(before[key] != snapshot[key] for key in CHANGE_FIELDS):
                changes.append(dict(subscription_id=row.id, before=before, after=snapshot,
                                    checked_at=slot))
            row.snapshot = json.dumps(snapshot)
            for favorite in favorites[(row.game, row.product_url)]:
                saved = json.loads(favorite.snapshot)
                # Subscriptions only fetch normal-condition offers.
                if (saved.get('cart') or {}).get('kizu', 0) != 0:
                    continue
                favorite.snapshot = json.dumps({**saved, **snapshot, 'product_url': favorite.product_url})
                favorite.refreshed_at = slot
        if changes:
            db.add(SubscriptionEmail(id=str(uuid.uuid4()), user_id=user_id, run_id=run_id, run_date=local_date,
                   changes=json.dumps(changes), state='pending', created_at=now, next_attempt_at=now))
        return len(changes)
