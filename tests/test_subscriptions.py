from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import json
import smtplib
import os
import time
from threading import Event

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select

from auth_helpers import register_user
from tcg_toolkit.db_models import Subscription, SubscriptionEmail, SubscriptionRun, User
from tcg_toolkit import subscriptions as api
from tcg_toolkit.subscription_worker import SubscriptionWorker, next_noon
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'
DAY = date(2026, 11, 1)
NOW = 1_800_000_000
URL = 'https://yuyu-tei.jp/sell/wsr/card/OS01/10240'


def snapshot(url=URL, price=100, quantity=5):
    return dict(game='wsr', name='<Card & name>', card_number='ABC-001', rarity='R',
                image_url=None, price=price, currency='JPY', stock_quantity=quantity,
                stock_status='in_stock' if quantity else 'out_of_stock', stock_text=str(quantity),
                product_url=url)


class Mailer:
    def __init__(self):
        self.messages = []
        self.error = None

    def send_message(self, message):
        if self.error:
            raise self.error
        self.messages.append(message)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    monkeypatch.setattr(api, 'fetch_product_snapshot', lambda game, url: snapshot(url))
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        login(client, 'alice')
        yield client


def login(client, username):
    response = register_user(client, username)
    assert response.status_code == 201
    client.headers['Authorization'] = 'Bearer ' + response.json()['token']
    return response.json()['user']['id']


def add(client, url=URL):
    response = client.post('/api/subscriptions', json=dict(game='wsr', product_url=url))
    assert response.status_code == 200, response.text
    return response.json()


def emails():
    with app.state.auth.store.session() as db:
        return db.scalars(select(SubscriptionEmail).order_by(SubscriptionEmail.created_at)).all()


def worker(fetcher=None, mailer=None, now=None):
    return SubscriptionWorker(app.state.auth.store, fetcher=fetcher or (lambda game, url: snapshot(url)),
                              mailer=mailer or Mailer(), clock=lambda: now[0] if now else NOW)


def test_subscription_idempotency_normalization_and_isolation(client, monkeypatch):
    saved = add(client, URL + '/')
    assert saved['item']['product_url'] == URL
    assert saved['item']['price'] == 100
    assert saved['checked_at'] is not None
    monkeypatch.setattr(api, 'fetch_product_snapshot', lambda *args: pytest.fail('duplicate fetch'))
    assert add(client) == saved
    response = client.get('/api/subscriptions')
    assert response.json()['items'] == [saved]
    assert response.headers['cache-control'] == 'no-store'
    token = client.headers['Authorization']
    login(client, 'bob')
    assert client.get('/api/subscriptions').json()['items'] == []
    path = '/api/subscriptions/' + saved['id']
    assert client.request('DELETE', path, json={}).status_code == 404
    client.headers['Authorization'] = token
    assert client.request('DELETE', path, json={}).status_code == 204
    assert client.get('/api/subscriptions').json()['items'] == []


@pytest.mark.parametrize('url', [
    URL.replace('https:', 'http:'), URL.replace('yuyu-tei.jp', 'evil.example'),
    URL.replace('/wsr/', '/ws/'), URL + '?next=1', URL + '#x',
    URL.replace('OS01', '..'), URL.replace('OS01', '%2e%2e'),
    URL.replace('yuyu-tei.jp', 'yuyu-tei.jp@localhost'), URL + '/more',
])
def test_invalid_urls_never_fetch(client, monkeypatch, url):
    monkeypatch.setattr(api, 'fetch_product_snapshot', lambda *args: pytest.fail('invalid URL fetched'))
    assert client.post('/api/subscriptions', json=dict(game='wsr', product_url=url)).status_code == 422


def test_guards_and_verified_email_required(client):
    body = dict(game='wsr', product_url=URL)
    assert client.post('/api/subscriptions', json=body, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/subscriptions', content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    with app.state.auth.store.session() as db:
        db.scalar(select(User).where(User.username == 'alice')).email_verified_at = None
    assert client.post('/api/subscriptions', json=body).json()['detail'] == 'verified_email_required'
    client.headers.pop('Authorization')
    for response in (client.get('/api/subscriptions'), client.post('/api/subscriptions', json=body),
                     client.request('DELETE', '/api/subscriptions/anything', json={})):
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'no-store'


def test_failed_initial_fetch_leaves_no_subscription(client, monkeypatch):
    def unavailable(*args):
        raise HTTPException(504, detail='product_timeout')
    monkeypatch.setattr(api, 'fetch_product_snapshot', unavailable)
    assert client.post('/api/subscriptions', json=dict(game='wsr', product_url=URL)).status_code == 504
    assert client.get('/api/subscriptions').json()['items'] == []


@pytest.mark.parametrize('price,quantity', [(110, 5), (90, 5), (100, 0), (100, 3), (100, 8)])
def test_changes_are_queued_once_and_sent_once(client, price, quantity):
    add(client)
    service = worker(lambda game, url: snapshot(url, price, quantity))
    assert service.check_daily(DAY)
    assert not service.check_daily(DAY)
    assert len(emails()) == 1
    change = json.loads(emails()[0].changes)[0]
    assert change['before']['price'] == 100 and change['after']['price'] == price
    assert change['before']['stock_quantity'] == 5 and change['after']['stock_quantity'] == quantity
    service.deliver_due()
    service.deliver_due()
    assert len(service.mailer.messages) == 1
    assert emails()[0].state == 'sent'
    message = service.mailer.messages[0]
    assert message['To'] == 'alice@example.com'
    plain = message.get_body(preferencelist=('plain',)).get_content()
    assert URL in plain and '<Card & name> · R · ABC-001' in plain and '/#favorites' in plain
    assert '&lt;Card &amp; name&gt;' in message.get_body(preferencelist=('html',)).get_content()


def test_unchanged_is_silent_and_restock_is_not(client):
    saved = add(client)
    service = worker()
    service.check_daily(DAY)
    service.deliver_due()
    assert emails() == [] and service.mailer.messages == []
    with app.state.auth.store.session() as db:
        db.get(Subscription, saved['id']).snapshot = json.dumps(snapshot(quantity=0))
    service.check_daily(DAY + timedelta(days=1))
    assert json.loads(emails()[0].changes)[0]['before']['stock_status'] == 'out_of_stock'


def test_many_cards_one_digest_per_user_and_shared_fetch(client):
    add(client)
    add(client, URL + '1')
    login(client, 'bob')
    add(client)
    calls = []
    def fetch(game, url):
        calls.append(url)
        return snapshot(url, price=200)
    service = worker(fetch)
    service.check_daily(DAY)
    service.deliver_due()
    assert sorted(calls) == [URL, URL + '1']
    assert sorted(len(json.loads(row.changes)) for row in emails()) == [1, 2]
    assert {message['To'] for message in service.mailer.messages} == {'alice@example.com', 'bob@example.com'}


@pytest.mark.parametrize('failure', ['product_timeout', 'product_unavailable', 'product_unreadable', 'unknown'])
def test_source_failure_preserves_baseline_and_other_cards_continue(client, failure):
    saved = add(client)
    add(client, URL + '1')
    def fetch(game, url):
        if url != URL:
            return snapshot(url, price=200)
        if failure == 'unknown':
            return {**snapshot(url), 'stock_quantity': None, 'stock_status': 'unknown'}
        raise HTTPException(502, detail=failure)
    service = worker(fetch)
    service.check_daily(DAY)
    with app.state.auth.store.session() as db:
        row = db.get(Subscription, saved['id'])
        assert row.snapshot == json.dumps(saved['item'])
        assert row.last_error and row.checked_at == NOW
    assert len(json.loads(emails()[0].changes)) == 1
    service.fetcher = lambda game, url: snapshot(url, price=200)
    service.check_daily(DAY + timedelta(days=1))
    assert len(emails()) == 2
    with app.state.auth.store.session() as db:
        assert db.get(Subscription, saved['id']).last_error is None


def test_retry_schedule_exhaustion_and_restart(client):
    add(client)
    now = [NOW]
    mailer = Mailer()
    mailer.error = smtplib.SMTPException('failure')
    service = worker(lambda game, url: snapshot(url, price=200), mailer, now)
    service.check_daily(DAY)
    for attempt, delay in enumerate((60, 300, 1800), start=1):
        service.deliver_due()
        row = emails()[0]
        assert row.attempts == attempt and row.state == 'pending'
        assert row.next_attempt_at == now[0] + delay
        service.deliver_due()
        assert emails()[0].attempts == attempt
        now[0] += delay
        service = worker(mailer=mailer, now=now)  # Process restart, durable schedule.
    service.deliver_due()
    assert emails()[0].state == 'failed' and emails()[0].attempts == 4
    mailer.error = None
    now[0] += 86400
    service.deliver_due()
    assert not mailer.messages


def test_retry_after_restart_retains_original_change(client):
    add(client)
    now = [NOW]
    service = worker(lambda game, url: snapshot(url, price=200), now=now)
    service.check_daily(DAY)
    service.mailer.error = OSError('offline')
    service.deliver_due()
    now[0] += 60
    restarted = worker(now=now)
    restarted.deliver_due()
    assert emails()[0].state == 'sent'
    assert '100 → 200 JPY' in restarted.mailer.messages[0].get_body(preferencelist=('plain',)).get_content()


@pytest.mark.parametrize('remove_all', [False, True])
def test_cancelled_subscriptions_are_excluded_from_pending_mail(client, remove_all):
    first = add(client)
    second = add(client, URL + '1')
    service = worker(lambda game, url: snapshot(url, price=200))
    service.check_daily(DAY)
    client.request('DELETE', '/api/subscriptions/' + first['id'], json={})
    if remove_all:
        client.request('DELETE', '/api/subscriptions/' + second['id'], json={})
    service.deliver_due()
    if remove_all:
        assert emails()[0].state == 'cancelled' and not service.mailer.messages
    else:
        body = service.mailer.messages[0].get_body(preferencelist=('plain',)).get_content()
        assert URL + '\n' not in body and URL + '1' in body


def test_atomic_snapshot_and_outbox_on_database_failure(client):
    saved = add(client)
    service = worker(lambda game, url: snapshot(url, price=200))
    def fail_insert(mapper, connection, target):
        raise RuntimeError('outbox unavailable')
    event.listen(SubscriptionEmail, 'before_insert', fail_insert)
    try:
        with pytest.raises(RuntimeError, match='outbox unavailable'):
            service.check_daily(DAY)
    finally:
        event.remove(SubscriptionEmail, 'before_insert', fail_insert)
    with app.state.auth.store.session() as db:
        assert json.loads(db.get(Subscription, saved['id']).snapshot)['price'] == 100
        assert db.get(SubscriptionRun, DAY).state == 'failed'
        assert db.scalar(select(func.count()).select_from(SubscriptionEmail)) == 0
    assert service.check_daily(DAY + timedelta(days=1))
    assert len(emails()) == 1


def test_concurrent_workers_do_not_repeat_checks_or_mail(client):
    add(client)
    entered, release = Event(), Event()
    def fetch(game, url):
        entered.set()
        assert release.wait(10)
        return snapshot(url, price=200)
    first, second = worker(fetch), worker()
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(first.check_daily, DAY)
        try:
            assert entered.wait(10)
            assert not second.check_daily(DAY)
        finally:
            release.set()
        assert running.result()
    entered.clear()
    release.clear()
    class BlockingMailer(Mailer):
        def send_message(self, message):
            entered.set()
            assert release.wait(10)
            super().send_message(message)
    first.mailer = BlockingMailer()
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(first.deliver_due)
        try:
            assert entered.wait(10)
            second.deliver_due()
            assert second.mailer.messages == []
        finally:
            release.set()
        running.result()
    assert len(first.mailer.messages) == 1 and emails()[0].attempts == 1


def test_noon_schedule_and_missed_startup_does_not_backfill(monkeypatch):
    before = datetime(2026, 11, 1, 11, 59, 59).timestamp()
    noon = datetime(2026, 11, 1, 12).timestamp()
    assert next_noon(before) == noon
    assert next_noon(noon) == datetime(2026, 11, 2, 12).timestamp()
    now = [before]
    service = worker(now=now)
    calls = []
    monkeypatch.setattr(service, 'check_daily', lambda day: calls.append(day) or True)
    monkeypatch.setattr(service, 'deliver_due', lambda: None)
    service.tick()
    assert calls == []
    now[0] = noon
    service.tick()
    service.tick()
    assert calls == [DAY]
    restarted = worker(now=[noon + 3600])
    assert restarted.deadline == datetime(2026, 11, 2, 12).timestamp()
    now[0] = datetime(2026, 11, 4, 13).timestamp()
    service.tick()
    assert calls == [DAY]


def test_busy_mail_worker_at_noon_does_not_skip_the_daily_check(client):
    add(client)
    now = [datetime(2026, 11, 1, 11, 59, 59).timestamp()]
    service = worker(lambda game, url: snapshot(url, price=200), now=now)
    other = worker()
    now[0] += 1
    with other.exclusive() as acquired:
        assert acquired
        service.tick()
        assert service.deadline == now[0]
        assert not emails()
    service.tick()
    assert len(emails()) == 1 and emails()[0].state == 'sent'


@pytest.mark.skipif(not hasattr(time, 'tzset'), reason='POSIX timezone switching; also checked in Linux Docker')
@pytest.mark.parametrize('day,hours', [(datetime(2026, 3, 7, 13), 22), (datetime(2026, 10, 31, 13), 24)])
def test_next_noon_observes_dst(day, hours):
    previous = os.environ.get('TZ')
    try:
        os.environ['TZ'] = 'America/Toronto'
        time.tzset()
        now = day.timestamp()
        due = next_noon(now)
        assert datetime.fromtimestamp(due).hour == 12
        assert due - now == hours * 3600
    finally:
        if previous is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = previous
        time.tzset()


def test_worker_lifecycle_is_explicitly_enabled(monkeypatch):
    import tcg_toolkit.web as web
    calls = []
    class Worker:
        def __init__(self, store):
            self.store = store
        def start(self):
            calls.append('start')
        def stop(self):
            # Store is still usable until the worker has stopped.
            with self.store.session() as db:
                db.scalar(select(func.count()).select_from(User))
            calls.append('stop')
    monkeypatch.setattr(web, 'SubscriptionWorker', Worker)
    with TestClient(app):
        assert calls == []
    monkeypatch.setenv('TCG_SUBSCRIPTIONS_ENABLED', 'true')
    with TestClient(app):
        assert calls == ['start']
    assert calls == ['start', 'stop']
