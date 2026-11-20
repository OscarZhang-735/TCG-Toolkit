from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from types import SimpleNamespace

from tcg_toolkit.catalog.errors import ProductSourceError
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select

from auth_helpers import register_user
from tcg_toolkit.persistence.models import Favorite, Subscription, SubscriptionEmail, SubscriptionRun, User
from tcg_toolkit.subscriptions import service as api
from tcg_toolkit.subscriptions.worker import SubscriptionWorker
from tcg_toolkit.web import app

# Subscription workers share a database-wide advisory lock.
pytestmark = pytest.mark.serial

ORIGIN = 'https://testserver'
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
def client(postgres_schema, monkeypatch):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    monkeypatch.setattr(api, 'time', SimpleNamespace(time=lambda: NOW - 86400))
    monkeypatch.setattr(api, 'fetch_product_snapshot', lambda game, url: snapshot(url))
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        login(client, 'alice')
        yield client


def login(client, username):
    response = register_user(client, username)
    assert response.status_code == 201
    client.headers['Authorization'] = 'Bearer ' + response.json()['token']
    return response.json()['user']['id']


def add(client, url=URL, interval_hours=None):
    if interval_hours is not None:
        assert client.put('/api/subscriptions/settings', json={'interval_hours': interval_hours}).status_code == 200
    response = client.post('/api/subscriptions', json=dict(game='wsr', product_url=url))
    assert response.status_code == 200, response.text
    return response.json()


def favorite(client, item=None):
    response = client.post('/api/favorites', json=item or snapshot())
    assert response.status_code == 200, response.text
    return response.json()


def emails():
    with app.state.database.session() as db:
        return db.scalars(select(SubscriptionEmail).order_by(SubscriptionEmail.created_at)).all()


def worker(fetcher=None, now=None):
    return SubscriptionWorker(app.state.database, fetcher=fetcher or (lambda game, url: snapshot(url)),
                              mailer=Mailer(), clock=lambda: now[0] if now else NOW)


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


def test_unfavorite_removes_only_the_matching_users_subscription(client):
    favorite = client.post('/api/favorites', json=snapshot(URL + '/'))
    assert favorite.status_code == 200
    add(client)
    unrelated = add(client, URL.replace('10240', '10241'))
    alice = client.headers['Authorization']
    login(client, 'bob')
    bobs_subscription = add(client)
    bob = client.headers['Authorization']

    path = '/api/favorites/' + favorite.json()['id']
    assert client.request('DELETE', path, json={}).status_code == 404
    client.headers['Authorization'] = alice
    assert client.request('DELETE', path, json={}).status_code == 204
    assert client.get('/api/favorites').json()['items'] == []
    assert client.get('/api/subscriptions').json()['items'] == [unrelated]
    client.headers['Authorization'] = bob
    assert client.get('/api/subscriptions').json()['items'] == [bobs_subscription]


def test_guards_and_verified_email_required(client):
    body = dict(game='wsr', product_url=URL)
    assert client.post('/api/subscriptions', json=body, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/subscriptions', content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    with app.state.database.session() as db:
        db.scalar(select(User).where(User.username == 'alice')).email_verified_at = None
    assert client.post('/api/subscriptions', json=body).json()['detail'] == 'verified_email_required'
    client.headers.pop('Authorization')
    for response in (client.get('/api/subscriptions'), client.post('/api/subscriptions', json=body),
                     client.request('DELETE', '/api/subscriptions/anything', json={})):
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('price,quantity', [(90, 5), (100, 0)])
def test_changes_are_queued_once_and_sent_once(client, price, quantity):
    add(client)
    service = worker(lambda game, url: snapshot(url, price, quantity))
    assert service.check_due()
    assert not service.check_due()
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
    service.check_due()
    service.deliver_due()
    assert emails() == [] and service.mailer.messages == []
    with app.state.database.session() as db:
        db.get(Subscription, saved['id']).snapshot = json.dumps(snapshot(quantity=0))
    service.clock = lambda: NOW + 86400
    service.check_due()
    assert json.loads(emails()[0].changes)[0]['before']['stock_status'] == 'out_of_stock'


def test_storefront_checks_refresh_favorites_and_send_one_mixed_digest(client, monkeypatch):
    urls = ['https://www.hobbystation-single.jp/ws/product/detail/387566',
            'https://www.cardmax.jp/shopdetail/000000160917']
    def offer(url, price):
        return {**snapshot(url, price), 'game': 'ws', 'rarity': None, 'stock_status': 'in_stock',
                'stock_quantity': None, 'stock_text': '在庫あり'}

    monkeypatch.setattr(api, 'fetch_product_snapshot', lambda game, url: offer(url, 100))
    for url in urls:
        favorite(client, {**snapshot(url), 'game': 'ws'})
        assert client.post('/api/subscriptions', json={'game': 'ws', 'product_url': url}).status_code == 200

    service = worker(lambda game, url: offer(url, 90))
    assert service.check_due()
    favorites = client.get('/api/favorites').json()['items']
    assert all(f['item']['price'] == 90 and f['item']['rarity'] == 'R' and f['refreshed_at'] == NOW
               for f in favorites)
    service.deliver_due()
    message, = service.mailer.messages
    html = message.get_body(preferencelist=('html',)).get_content()
    plain = message.get_body(preferencelist=('plain',)).get_content()
    assert all(url in html and url in plain for url in urls)
    assert 'View on Hobby Station' in html and 'View on Card Max' in html
    assert '有货 In stock' in html and '有货 In stock → 有货 In stock' in plain


@pytest.mark.parametrize('failure', ['product_timeout', 'unknown'])
def test_source_failure_preserves_baseline_and_other_cards_continue(client, failure):
    failed_favorite = favorite(client)
    other_favorite = favorite(client, snapshot(URL + '1'))
    saved = add(client)
    add(client, URL + '1')
    def fetch(game, url):
        if url != URL:
            return snapshot(url, price=200)
        if failure == 'unknown':
            return {**snapshot(url), 'stock_quantity': None, 'stock_status': 'unknown'}
        raise ProductSourceError(failure)
    service = worker(fetch)
    service.check_due()
    with app.state.database.session() as db:
        row = db.get(Subscription, saved['id'])
        assert row.snapshot == json.dumps(saved['item'])
        assert row.last_error and row.checked_at == NOW
    assert len(json.loads(emails()[0].changes)) == 1
    assert client.get('/api/favorites').json()['items'] == [
        {**other_favorite, 'item': snapshot(URL + '1', price=200), 'refreshed_at': NOW},
        failed_favorite,
    ]
    service.fetcher = lambda game, url: snapshot(url, price=200)
    service.clock = lambda: NOW + 86400
    service.check_due()
    assert len(emails()) == 2
    with app.state.database.session() as db:
        assert db.get(Subscription, saved['id']).last_error is None
        refreshed = db.get(Favorite, failed_favorite['id'])
        assert json.loads(refreshed.snapshot) == snapshot(price=200)
        assert refreshed.refreshed_at == NOW + 86400


def test_retry_after_restart_retains_original_change(client):
    add(client)
    now = [NOW]
    service = worker(lambda game, url: snapshot(url, price=200), now=now)
    service.check_due()
    service.mailer.error = OSError('offline')
    service.deliver_due()
    now[0] += 60
    restarted = worker(now=now)
    restarted.deliver_due()
    assert emails()[0].state == 'sent'
    assert '100 → 200 JPY' in restarted.mailer.messages[0].get_body(preferencelist=('plain',)).get_content()


def test_atomic_snapshot_and_outbox_on_database_failure(client):
    saved_favorite = favorite(client)
    saved = add(client)
    service = worker(lambda game, url: snapshot(url, price=200))
    def fail_insert(mapper, connection, target):
        raise RuntimeError('outbox unavailable')
    event.listen(SubscriptionEmail, 'before_insert', fail_insert)
    try:
        with pytest.raises(RuntimeError, match='outbox unavailable'):
            service.check_due()
    finally:
        event.remove(SubscriptionEmail, 'before_insert', fail_insert)
    with app.state.database.session() as db:
        assert json.loads(db.get(Subscription, saved['id']).snapshot)['price'] == 100
        assert db.scalar(select(SubscriptionRun)).state == 'failed'
        assert db.scalar(select(func.count()).select_from(SubscriptionEmail)) == 0
    assert client.get('/api/favorites').json()['items'] == [saved_favorite]
    assert service.check_due()
    assert len(emails()) == 1
    assert client.get('/api/favorites').json()['items'] == [
        {**saved_favorite, 'item': snapshot(price=200), 'refreshed_at': NOW},
    ]


def test_concurrent_workers_do_not_repeat_checks_or_mail(client):
    add(client)
    entered, release = Event(), Event()
    def fetch(game, url):
        entered.set()
        assert release.wait(10)
        return snapshot(url, price=200)
    first, second = worker(fetch), worker()
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(first.check_due)
        try:
            assert entered.wait(10)
            assert not second.check_due()
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
