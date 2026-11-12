"""Hourly due checks and durable SMTP digests, serialized through PostgreSQL."""
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from email.message import EmailMessage
from .email_templates import digest_html, digest_subject, digest_titles, favorites_url
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
from .notification_email import notification_recipient

logger = logging.getLogger('uvicorn.error.subscriptions')
LOCK_KEY = 894210612
RETRY_DELAYS = (60, 300, 1800)
# A scan that starts this late still counts as its scheduled hour (lock waits, wake-up jitter).
SLOT_TOLERANCE = 600
CHANGE_FIELDS = ('price', 'stock_quantity', 'stock_status')


def next_hour(now):
    """Find the next local hour boundary on the timeline, including DST folds."""
    candidate = (int(now) // 60 + 1) * 60
    while datetime.fromtimestamp(candidate).minute != 0:
        candidate += 60
    return candidate


def scheduled_scan(due, now):
    """Estimate the hourly scan that first sees a subscription due at `due`."""
    moment = max(int(due), int(now))
    try:
        return next_hour(moment - 1)  # A boundary itself is a scan time.
    except (OverflowError, OSError, ValueError):  # Beyond datetime's range.
        return moment


def digest_message(recipient, email_id, run_date, changes):
    message = EmailMessage()
    message['To'] = recipient
    message['Subject'] = digest_subject(run_date)
    message['Message-ID'] = f'<{email_id}@tcg-toolkit.local>'
    # Bilingual like the HTML part: Chinese first, then English, line by line.
    zh_title, en_title = digest_titles(len(changes))
    text = [f'{run_date} {zh_title}（普通品相）', f'{run_date} {en_title} (normal condition)', '']
    for change in changes:
        before, after = change['before'], change['after']
        title = ' · '.join(filter(None, [after['name'], after.get('rarity'), after['card_number']]))
        price = f"价格 Price：{before['price']:,} → {after['price']:,} JPY"
        quantity = f"库存 Stock：{before['stock_quantity'] or 0:,} → {after['stock_quantity'] or 0:,}"
        checked = '检查时间 Checked at：' + datetime.fromtimestamp(change['checked_at']).astimezone().isoformat(timespec='seconds')
        url = after['product_url']
        text.extend([title, price, quantity, checked, url, ''])
    text.append(f'前往 TCG Toolkit 查看 · Open TCG Toolkit：{favorites_url()}')
    message.set_content('\n'.join(text))
    message.add_alternative(digest_html(run_date, changes), subtype='html')
    return message


class SubscriptionWorker:
    def __init__(self, store, *, fetcher=None, mailer=None, clock=time.time):
        self.store = store
        self.fetcher = fetcher or fetch_product_snapshot
        self.mailer = mailer if mailer is not None else SMTPMailer()
        self.clock = clock
        self.stopping = Event()
        self.thread = None
        self.deadline = clock()  # Scan overdue subscriptions immediately on startup.

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

    def check_due(self, slot=None):
        with self.exclusive() as acquired:
            if not acquired:
                return None
            started_at = int(self.clock())
            # Anchor schedules to the planned hour, so a scan starting at HH:00:01
            # does not push the next one past HH:00:00 and slip a whole hour.
            slot = int(slot) if slot is not None and 0 <= started_at - int(slot) <= SLOT_TOLERANCE else started_at
            with self.store.session() as db:
                subscriptions = db.scalars(select(Subscription).where(
                    Subscription.next_check_at <= started_at
                ).order_by(Subscription.user_id, Subscription.id)).all()
                if not subscriptions:
                    return False
                run_id = str(uuid.uuid4())
                local_date = datetime.fromtimestamp(started_at).date()
                db.add(SubscriptionRun(id=run_id, local_date=local_date, started_at=started_at, state='running'))
            try:
                self._check_cards(subscriptions, run_id, local_date, started_at, slot)
            except Exception as exc:
                with self.store.session() as db:
                    run = db.get(SubscriptionRun, run_id)
                    run.state, run.last_error = 'failed', type(exc).__name__
                    run.finished_at = int(self.clock())
                raise
            with self.store.session() as db:
                run = db.get(SubscriptionRun, run_id)
                run.state, run.finished_at = 'complete', int(self.clock())
            return True

    def _check_cards(self, subscriptions, run_id, local_date, started_at, slot):
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
                products[(game, url)] = (snapshot, None)
            except HTTPException as exc:
                products[(game, url)] = (None, str(exc.detail))
                errors += 1
        changed = 0
        for user_id, ids in by_user.items():
            if self.stopping.is_set():
                raise InterruptedError('Subscription checks stopped')
            with self.store.session() as db:
                # Match settings/creation lock order: account, then subscriptions.
                account = db.get(User, user_id, with_for_update=True)
                if account is None:
                    continue
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
                if changes:
                    now = int(self.clock())
                    db.add(SubscriptionEmail(id=str(uuid.uuid4()), user_id=user_id, run_id=run_id, run_date=local_date,
                           changes=json.dumps(changes), state='pending', created_at=now, next_attempt_at=now))
                    changed += len(changes)
        logger.info('Subscription check run=%s cards=%d changes=%d source_errors=%d',
                    run_id, len(products), changed, errors)

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
            recipient = notification_recipient(user)
            if not changes or not recipient:
                email.state = 'cancelled'
                return
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
            # A busy worker retains the deadline and retries after releasing the lock.
            # Failed rounds retry on the next hour; committed rows are no longer due.
            try:
                result = self.check_due(self.deadline)
            except Exception:
                self.deadline = next_hour(self.clock())
                raise
            if result is not None:
                self.deadline = next_hour(self.clock())
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
