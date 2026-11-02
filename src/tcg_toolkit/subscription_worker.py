"""Local-noon checks and durable SMTP digests, serialized through PostgreSQL."""
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.message import EmailMessage
from html import escape
import json
import logging
import smtplib
from threading import Event, Thread
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select

from .auth.email_verification import SMTPMailer
from .db_models import Subscription, SubscriptionEmail, SubscriptionRun, User
from .product_details import fetch_product_snapshot

logger = logging.getLogger('uvicorn.error.subscriptions')
LOCK_KEY = 894210612
RETRY_DELAYS = (60, 300, 1800)
CHANGE_FIELDS = ('price', 'stock_quantity', 'stock_status')


def next_noon(now):
    """Use a naive local datetime so timestamp() applies DST for the target day."""
    local = datetime.fromtimestamp(now)
    noon = local.replace(hour=12, minute=0, second=0, microsecond=0)
    if noon <= local:
        noon += timedelta(days=1)
    return noon.timestamp()


def digest_message(recipient, email_id, run_date, changes):
    message = EmailMessage()
    message['To'] = recipient
    message['Subject'] = f'TCG Toolkit 卡牌更新 · {run_date}'
    message['Message-ID'] = f'<{email_id}@tcg-toolkit.local>'
    text = [f'{run_date} 卡牌订阅更新（普通品相）', '']
    html = [f'<h1>{run_date} 卡牌订阅更新</h1><p>普通品相 · 价格单位：JPY</p>']
    for change in changes:
        before, after = change['before'], change['after']
        title = f"{after['name']} · {after['card_number']}"
        price = f"价格：{before['price']:,} → {after['price']:,} JPY"
        def stock(item):
            return f"{item['stock_quantity']}（{'有货' if item['stock_status'] == 'in_stock' else '缺货'}）"
        quantity = f'库存：{stock(before)} → {stock(after)}'
        checked = '检查时间：' + datetime.fromtimestamp(change['checked_at']).astimezone().isoformat(timespec='seconds')
        url = after['product_url']
        text.extend([title, price, quantity, checked, url, ''])
        html.append(f'<h2>{escape(title)}</h2><p>{escape(price)}<br>{escape(quantity)}'
                    f'<br>{escape(checked)}</p><p><a href="{escape(url, quote=True)}">游游亭详情页</a></p>')
    message.set_content('\n'.join(text))
    message.add_alternative('<html><body>' + ''.join(html) + '</body></html>', subtype='html')
    return message


class SubscriptionWorker:
    def __init__(self, store, *, fetcher=None, mailer=None, clock=time.time):
        self.store = store
        self.fetcher = fetcher or fetch_product_snapshot
        self.mailer = mailer if mailer is not None else SMTPMailer()
        self.clock = clock
        self.stopping = Event()
        self.thread = None
        self.deadline = next_noon(clock())

    @contextmanager
    def exclusive(self):
        # A dedicated session lock spans network I/O, without a long transaction.
        with self.store.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            acquired = conn.scalar(select(func.pg_try_advisory_lock(LOCK_KEY)))
            try:
                yield acquired
            finally:
                if acquired:
                    try:
                        conn.execute(select(func.pg_advisory_unlock(LOCK_KEY)))
                    except Exception:
                        conn.invalidate()  # Never return a locked connection to the pool.
                        raise

    def check_daily(self, local_date):
        with self.exclusive() as acquired:
            if not acquired:
                return None
            with self.store.session() as db:
                if db.get(SubscriptionRun, local_date) is not None:
                    return False
                db.add(SubscriptionRun(local_date=local_date, started_at=int(self.clock()), state='running'))
            try:
                self._check_cards(local_date)
            except Exception as exc:
                with self.store.session() as db:
                    run = db.get(SubscriptionRun, local_date)
                    run.state, run.last_error = 'failed', type(exc).__name__
                    run.finished_at = int(self.clock())
                raise
            with self.store.session() as db:
                run = db.get(SubscriptionRun, local_date)
                run.state, run.finished_at = 'complete', int(self.clock())
            return True

    def _check_cards(self, local_date):
        with self.store.session() as db:
            subscriptions = db.scalars(select(Subscription).order_by(Subscription.user_id, Subscription.id)).all()
        by_user, products = defaultdict(list), {}
        for row in subscriptions:
            by_user[row.user_id].append(row.id)
            products[(row.game, row.product_url)] = None
        errors = 0
        for game, url in products:
            if self.stopping.is_set():
                raise InterruptedError('Subscription checks stopped')
            try:
                snapshot = self.fetcher(game, url)
                if (snapshot['stock_status'] == 'unknown' or snapshot['stock_quantity'] is None
                        or snapshot['price'] is None):
                    raise HTTPException(502, detail='product_unreadable')
                products[(game, url)] = (snapshot, None, int(self.clock()))
            except HTTPException as exc:
                products[(game, url)] = (None, str(exc.detail), int(self.clock()))
                errors += 1
        changed = 0
        for user_id, ids in by_user.items():
            if self.stopping.is_set():
                raise InterruptedError('Subscription checks stopped')
            with self.store.session() as db:
                rows = db.scalars(select(Subscription).where(Subscription.id.in_(ids))
                                  .order_by(Subscription.id).with_for_update()).all()
                changes = []
                for row in rows:
                    snapshot, error, checked_at = products[(row.game, row.product_url)]
                    row.checked_at, row.last_error = checked_at, error
                    if error:
                        continue
                    before = json.loads(row.snapshot)
                    if any(before[key] != snapshot[key] for key in CHANGE_FIELDS):
                        changes.append(dict(subscription_id=row.id, before=before, after=snapshot,
                                            checked_at=checked_at))
                    row.snapshot = json.dumps(snapshot)
                if changes:
                    now = int(self.clock())
                    db.add(SubscriptionEmail(id=str(uuid.uuid4()), user_id=user_id, run_date=local_date,
                           changes=json.dumps(changes), state='pending', created_at=now, next_attempt_at=now))
                    changed += len(changes)
        logger.info('Subscription check date=%s cards=%d changes=%d source_errors=%d',
                    local_date, len(products), changed, errors)

    def deliver_due(self):
        with self.exclusive() as acquired:
            if not acquired:
                return
            with self.store.session() as db:
                ids = db.scalars(select(SubscriptionEmail.id).where(
                    SubscriptionEmail.state == 'pending', SubscriptionEmail.next_attempt_at <= int(self.clock())
                ).order_by(SubscriptionEmail.created_at, SubscriptionEmail.id)).all()
            for email_id in ids:
                if self.stopping.is_set():
                    break
                self._deliver(email_id)

    def _deliver(self, email_id):
        with self.store.session() as db:
            email = db.get(SubscriptionEmail, email_id)
            user = db.get(User, email.user_id)
            active = set(db.scalars(select(Subscription.id).where(Subscription.user_id == email.user_id)))
            changes = [item for item in json.loads(email.changes) if item['subscription_id'] in active]
            if not changes or not user or not user.email or user.email_verified_at is None:
                email.state = 'cancelled'
                return
            recipient = user.email
        message = digest_message(recipient, email.id, email.run_date, changes)
        error = None
        try:
            self.mailer.send_message(message)
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            error = type(exc).__name__
        with self.store.session() as db:
            row = db.get(SubscriptionEmail, email_id)
            row.attempts += 1
            row.last_error = error
            now = int(self.clock())
            if error is None:
                row.state, row.sent_at = 'sent', now
            elif row.attempts <= len(RETRY_DELAYS):
                row.next_attempt_at = now + RETRY_DELAYS[row.attempts - 1]
            else:
                row.state = 'failed'
            log = logger.error if row.state == 'failed' else logger.info
            log('Subscription email id=%s state=%s attempts=%d error=%s', email_id, row.state, row.attempts, error)

    def tick(self):
        now = self.clock()
        if now >= self.deadline:
            due_date = datetime.fromtimestamp(self.deadline).date()
            # A suspended process that wakes on a later day must not backfill.
            if datetime.fromtimestamp(now).date() == due_date:
                # Delivery in another process may own the lock at noon. Keep
                # the deadline until a daily record exists or this check runs.
                if self.check_daily(due_date) is not None:
                    self.deadline = next_noon(self.clock())
            else:
                self.deadline = next_noon(now)
        self.deliver_due()

    def _run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception('Subscription worker iteration failed')
            self.stopping.wait(max(1, min(30, self.deadline - self.clock())))

    def start(self):
        logger.info('Subscription scheduler timezone=%s next_check=%s', time.tzname,
                    datetime.fromtimestamp(self.deadline).astimezone().isoformat())
        self.thread = Thread(target=self._run, name='card-subscriptions', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join()
