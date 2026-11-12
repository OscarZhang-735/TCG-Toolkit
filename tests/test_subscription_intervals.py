from datetime import datetime
import json

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from tcg_toolkit import subscriptions as api
from tcg_toolkit.db_models import Subscription, SubscriptionEmail, SubscriptionRun, User
from tcg_toolkit.web import app
from test_subscriptions import client, add, worker, emails, snapshot, login, NOW, URL, ORIGIN


def test_default_create_and_update_interval(client, monkeypatch):
    now = [NOW]
    monkeypatch.setattr(api.time, 'time', lambda: now[0])
    def fetch(*args):
        now[0] += 120
        return snapshot()
    monkeypatch.setattr(api, 'fetch_product_snapshot', fetch)
    saved = client.post('/api/subscriptions', json={'game': 'wsr', 'product_url': URL}).json()
    assert client.get('/api/subscriptions/settings').json() == {'interval_hours': 24, 'next_check_at': NOW + 86400}
    assert 'interval_hours' not in saved
    assert saved['checked_at'] == NOW
    assert saved['next_check_at'] == NOW + 86400
    monkeypatch.setattr(api, 'fetch_product_snapshot', lambda *args: pytest.fail('unexpected fetch'))
    assert add(client) == saved
    path = '/api/subscriptions/settings'
    now[0] += 7200
    changed = client.put(path, json={'interval_hours': 1})
    assert changed.status_code == 200
    assert changed.headers['cache-control'] == 'no-store'
    # Already overdue: the next hourly scan picks it up.
    assert changed.json() == {'interval_hours': 1, 'next_check_at': NOW + 3 * 3600}
    assert client.get('/api/subscriptions').json()['items'][0]['next_check_at'] == NOW + 3600
    assert client.put(path, json={'interval_hours': 1}).json() == changed.json()
    assert client.get('/api/subscriptions').json()['items'][0]['next_check_at'] == NOW + 3600
    extended = client.put(path, json={'interval_hours': 6}).json()
    assert extended == {'interval_hours': 6, 'next_check_at': NOW + 21600}
    assert client.get('/api/subscriptions').json()['items'][0]['next_check_at'] == NOW + 21600
    login(client, 'bob')
    assert client.get(path).json() == {'interval_hours': 24, 'next_check_at': None}
    assert client.patch('/api/subscriptions/' + saved['id'], json={'interval_hours': 1}).status_code == 405


@pytest.mark.parametrize('interval', [0, -1, 1.5, 1.0, True, '6', None, 2147483648])
def test_invalid_intervals(client, interval):
    saved = add(client)
    assert client.put('/api/subscriptions/settings', json={'interval_hours': interval}).status_code == 422
    assert client.post('/api/subscriptions', json={
        'game': 'wsr', 'product_url': URL + '1', 'interval_hours': interval}).status_code == 422
    assert client.get('/api/subscriptions').json()['items'] == [saved]


def test_update_guards_and_cors(client):
    path = '/api/subscriptions/settings'
    assert client.put(path, json={'interval_hours': 1}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.put(path, content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    assert client.put(path, json={}).status_code == 422
    response = client.options(path, headers={'Origin': ORIGIN, 'Access-Control-Request-Method': 'PUT',
                                            'Access-Control-Request-Headers': 'content-type,authorization'})
    assert response.status_code == 200
    client.headers.pop('Authorization')
    assert client.put(path, json={'interval_hours': 1}).status_code == 401


def test_database_rejects_zero_interval(client):
    saved = add(client)
    with pytest.raises(IntegrityError):
        with app.state.auth.store.session() as db:
            row = db.get(Subscription, saved['id'])
            db.get(User, row.user_id).subscription_interval_hours = 0


def test_settings_apply_to_all_existing_and_future_subscriptions(client):
    first = add(client)
    second = add(client, URL + '1')
    alice = client.headers['Authorization']
    login(client, 'bob')
    other = add(client)
    bob = client.headers['Authorization']
    client.headers['Authorization'] = alice
    assert client.put('/api/subscriptions/settings', json={'interval_hours': 6}).status_code == 200
    records = client.get('/api/subscriptions').json()['items']
    assert {r['id'] for r in records} == {first['id'], second['id']}
    assert all(r['next_check_at'] == r['checked_at'] + 21600 for r in records)
    third = add(client, URL + '2')
    assert third['next_check_at'] == third['checked_at'] + 21600
    assert client.post('/api/subscriptions', json={
        'game': 'wsr', 'product_url': URL + '3', 'interval_hours': 1}).status_code == 422
    assert client.patch('/api/subscriptions/' + first['id'], json={'interval_hours': 1}).status_code == 405
    client.headers['Authorization'] = bob
    settings = client.get('/api/subscriptions/settings').json()
    assert settings['interval_hours'] == 24
    assert other['next_check_at'] <= settings['next_check_at'] < other['next_check_at'] + 3600
    assert client.get('/api/subscriptions').json()['items'] == [other]


def test_creation_uses_settings_changed_during_initial_fetch(client, monkeypatch):
    def fetch(*args):
        assert client.put('/api/subscriptions/settings', json={'interval_hours': 6}).status_code == 200
        return snapshot()
    monkeypatch.setattr(api, 'fetch_product_snapshot', fetch)
    saved = add(client)
    assert saved['next_check_at'] == saved['checked_at'] + 21600


def test_account_setting_and_schedules_roll_back_together(client):
    saved = add(client)
    def fail_update(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('UPDATE subscriptions SET'):
            raise RuntimeError('schedule update failed')
    engine = app.state.auth.store.engine
    event.listen(engine, 'before_cursor_execute', fail_update)
    try:
        with pytest.raises(RuntimeError, match='schedule update failed'):
            client.put('/api/subscriptions/settings', json={'interval_hours': 1})
    finally:
        event.remove(engine, 'before_cursor_execute', fail_update)
    assert client.get('/api/subscriptions/settings').json()['interval_hours'] == 24
    assert client.get('/api/subscriptions').json()['items'] == [saved]


def test_mixed_intervals_and_same_day_digests(client, monkeypatch):
    start = int(datetime(2026, 11, 1, 0).timestamp())
    now = [start]
    monkeypatch.setattr(api.time, 'time', lambda: now[0])
    saved = []
    for hours in (1, 6, 24):
        login(client, f'user{hours}')
        saved.append(add(client, URL + str(hours), hours))
    calls = []
    def fetch(game, url):
        calls.append((now[0], url))
        return snapshot(url, price=100 + int((now[0] - start) / 3600))
    service = worker(fetch, now=now)
    service.tick()
    assert not calls
    with app.state.auth.store.session() as db:
        assert db.scalar(select(func.count()).select_from(SubscriptionRun)) == 0
    for hour in range(1, 25):
        now[0] = start + hour * 3600
        service.tick()
        service.tick()  # No duplicate execution in the same hour.
    assert [sum(url == item['item']['product_url'] for _, url in calls) for item in saved] == [24, 4, 1]
    assert len(emails()) == 29
    assert len({email.run_id for email in emails()}) == 24
    assert all(email.state == 'sent' for email in emails())
    assert len(service.mailer.messages) == 29


def test_shared_product_leaves_not_due_subscription_untouched(client, monkeypatch):
    first = add(client, interval_hours=1)
    login(client, 'bob')
    second = add(client, interval_hours=1)
    login(client, 'carol')
    monkeypatch.setattr(api.time, 'time', lambda: NOW)
    future = add(client, interval_hours=6)
    calls = []
    service = worker(lambda game, url: calls.append(url) or snapshot(url, price=200))
    service.check_due()
    assert calls == [URL]
    assert client.get('/api/subscriptions').json()['items'] == [future]
    assert {change['subscription_id'] for email in emails()
            for change in json.loads(email.changes)} == {first['id'], second['id']}


@pytest.mark.parametrize('action', ['cancel', 'extend', 'shorten'])
def test_subscription_change_during_fetch(client, action):
    saved = add(client)
    path = '/api/subscriptions/' + saved['id']
    def fetch(game, url):
        if action == 'cancel':
            assert client.request('DELETE', path, json={}).status_code == 204
        else:
            assert client.put('/api/subscriptions/settings', json={'interval_hours': 48 if action == 'extend' else 1}).status_code == 200
        return snapshot(url, price=200)
    worker(fetch).check_due()
    records = client.get('/api/subscriptions').json()['items']
    if action == 'cancel':
        assert records == [] and emails() == []
    elif action == 'extend':
        assert records[0]['checked_at'] == saved['checked_at']
        assert records[0]['item'] == saved['item'] and emails() == []
        assert records[0]['next_check_at'] == saved['checked_at'] + 48 * 3600
    else:
        assert records[0]['next_check_at'] == NOW + 3600
        assert records[0]['item']['price'] == 200 and len(emails()) == 1


def test_slow_fetch_keeps_next_hour_and_failure_interval(client, monkeypatch):
    start = int(datetime(2026, 11, 1, 10).timestamp())
    now = [start]
    monkeypatch.setattr(api.time, 'time', lambda: start - 3600)
    saved = add(client, interval_hours=1)
    def fetch(game, url):
        now[0] += 120
        return snapshot(url, price=200)
    service = worker(fetch, now=now)
    service.tick()
    record = client.get('/api/subscriptions').json()['items'][0]
    assert record['checked_at'] == start and record['next_check_at'] == start + 3600
    assert service.deadline == start + 3600
    from fastapi import HTTPException
    def failed(*args):
        raise HTTPException(504, detail='product_timeout')
    service.fetcher = failed
    client.put('/api/subscriptions/settings', json={'interval_hours': 6})
    now[0] = start + 6 * 3600
    service.tick()
    record = client.get('/api/subscriptions').json()['items'][0]
    assert record['checked_at'] == now[0] and record['next_check_at'] == now[0] + 6 * 3600
    assert record['item']['price'] == 200 and record['last_error'] == 'product_timeout'


def test_restart_catches_up_once(client):
    add(client, interval_hours=1)
    now = [NOW + 7 * 86400]
    calls = []
    service = worker(lambda game, url: calls.append(url) or snapshot(url, price=200), now=now)
    service.tick()
    worker(lambda *args: pytest.fail('duplicate catchup'), now=now).tick()
    assert calls == [URL] and len(emails()) == 1
    assert client.get('/api/subscriptions').json()['items'][0]['next_check_at'] == now[0] + 3600


def test_partial_user_commit_recovers_remaining_due_rows(client):
    add(client)
    login(client, 'bob')
    add(client, URL + '1')
    service = worker(lambda game, url: snapshot(url, price=200))
    inserted = []
    def fail_second(mapper, connection, target):
        inserted.append(target.user_id)
        if len(inserted) == 2:
            raise RuntimeError('second user failed')
    event.listen(SubscriptionEmail, 'before_insert', fail_second)
    try:
        with pytest.raises(RuntimeError, match='second user failed'):
            service.tick()
    finally:
        event.remove(SubscriptionEmail, 'before_insert', fail_second)
    assert len(emails()) == 1
    calls = []
    service.fetcher = lambda game, url: calls.append(url) or snapshot(url, price=200)
    service.clock = lambda: service.deadline
    service.tick()
    assert len(calls) == 1 and len(emails()) == 2
    assert len({email.user_id for email in emails()}) == 2
    with app.state.auth.store.session() as db:
        assert {run.state for run in db.scalars(select(SubscriptionRun))} == {'failed', 'complete'}


def test_next_check_estimate_rounds_to_the_hourly_scan(client, monkeypatch):
    start = int(datetime(2026, 11, 1, 10).timestamp())
    monkeypatch.setattr(api.time, 'time', lambda: start + 25 * 60)  # Created at 10:25.
    add(client, interval_hours=6)
    add(client, URL + '1')
    path = '/api/subscriptions/settings'
    # The earliest due card (16:25) is first seen by the 17:00 scan.
    assert client.get(path).json() == {'interval_hours': 6, 'next_check_at': start + 7 * 3600}
    monkeypatch.setattr(api.time, 'time', lambda: start + 30 * 3600 + 600)  # Overdue: the next scan.
    assert client.get(path).json()['next_check_at'] == start + 31 * 3600
    monkeypatch.setattr(api.time, 'time', lambda: start + 31 * 3600)  # A scan is running right now.
    assert client.get(path).json()['next_check_at'] == start + 31 * 3600
    assert client.put(path, json={'interval_hours': 2147483647}).json()['interval_hours'] == 2147483647


def test_scan_jitter_and_lock_waits_keep_the_hourly_cadence(client, monkeypatch):
    start = int(datetime(2026, 11, 1, 10).timestamp())
    monkeypatch.setattr(api.time, 'time', lambda: start - 3600)
    add(client, interval_hours=1)
    monkeypatch.setattr(api.time, 'time', lambda: now[0])
    now = [start]
    calls = []
    service = worker(lambda game, url: calls.append(now[0]) or snapshot(url), now=now)
    service.deadline = start
    now[0] = start + 1  # Woke one second late.
    service.tick()
    record = client.get('/api/subscriptions').json()['items'][0]
    assert record['checked_at'] == start and record['next_check_at'] == start + 3600
    now[0] = start + 3600  # Exactly on time: still due.
    service.tick()
    now[0] = start + 2 * 3600 + 45  # Waited behind email delivery for 45 seconds.
    service.tick()
    assert calls == [start + 1, start + 3600, start + 2 * 3600 + 45]
    assert client.get('/api/subscriptions').json()['items'][0]['next_check_at'] == start + 3 * 3600
    assert client.get('/api/subscriptions/settings').json()['next_check_at'] == start + 3 * 3600
