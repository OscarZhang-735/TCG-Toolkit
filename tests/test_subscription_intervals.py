import json

import pytest

from tcg_toolkit.subscriptions import service as api
from tcg_toolkit.persistence.models import Favorite
from tcg_toolkit.web import app
from test_subscriptions import client, add, favorite, worker, emails, snapshot, login, NOW, URL

# Subscription workers share a database-wide advisory lock.
pytestmark = pytest.mark.serial


def test_settings_apply_to_all_existing_and_future_subscriptions(client):
    first = add(client)
    second = add(client, URL + '1')
    alice = client.headers['Authorization']
    login(client, 'bob')
    other = add(client)
    bob = client.headers['Authorization']
    client.headers['Authorization'] = alice
    assert client.put('/api/subscriptions/settings', json={'interval_hours': 0}).status_code == 422
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


def test_shared_product_leaves_not_due_subscription_untouched(client, monkeypatch):
    favorites = [favorite(client)]
    first = add(client, interval_hours=1)
    login(client, 'bob')
    favorites.append(favorite(client))
    second = add(client, interval_hours=1)
    login(client, 'carol')
    future_favorite = favorite(client)
    monkeypatch.setattr(api.time, 'time', lambda: NOW)
    future = add(client, interval_hours=6)
    calls = []
    service = worker(lambda game, url: calls.append(url) or snapshot(url, price=200))
    service.check_due()
    assert calls == [URL]
    assert client.get('/api/subscriptions').json()['items'] == [future]
    assert client.get('/api/favorites').json()['items'] == [future_favorite]
    with app.state.database.session() as db:
        for saved in favorites:
            row = db.get(Favorite, saved['id'])
            assert json.loads(row.snapshot) == snapshot(price=200)
            assert row.refreshed_at == NOW
    assert {change['subscription_id'] for email in emails()
            for change in json.loads(email.changes)} == {first['id'], second['id']}


def test_restart_catches_up_once(client):
    add(client, interval_hours=1)
    now = [NOW + 7 * 86400]
    calls = []
    service = worker(lambda game, url: calls.append(url) or snapshot(url, price=200), now=now)
    service.tick()
    worker(lambda *args: pytest.fail('duplicate catchup'), now=now).tick()
    assert calls == [URL] and len(emails()) == 1
    assert client.get('/api/subscriptions').json()['items'][0]['next_check_at'] == now[0] + 3600
