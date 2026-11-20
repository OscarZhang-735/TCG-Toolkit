import time

import pytest

from tcg_toolkit.persistence.models import EmailChallenge
from tcg_toolkit.web import app
from test_subscriptions import client, add, worker, emails, snapshot, NOW, login

# Subscription workers share a database-wide advisory lock.
pytestmark = pytest.mark.serial

PATH = '/api/subscriptions/notification-email'


def request_code(client, email='alerts@example.com'):
    response = client.post(PATH + '/code', json={'email': email, 'language': 'zh-CN'})
    assert response.status_code == 200, response.text
    mailbox = app.state.auth.verification.mailer
    assert mailbox.messages[-1]['purpose'] == 'notification_email'
    return {'challenge_id': response.json()['challenge_id'], 'code': mailbox.messages[-1]['code']}


def test_default_verify_change_reset_and_account_identity(client):
    original = client.get('/api/auth/me').json()
    expected = {'email': 'alice@example.com', 'custom_email': None, 'using_registration_email': True}
    assert client.get(PATH).json() == expected
    code = request_code(client, ' ALERTS@EXAMPLE.COM ')
    assert client.get(PATH).json() == expected  # pending is not effective
    result = client.put(PATH, json=code)
    assert result.status_code == 200
    assert result.json() == {'email': 'alerts@example.com', 'custom_email': 'alerts@example.com',
                             'using_registration_email': False}
    assert client.get('/api/auth/me').json() == original
    assert client.put(PATH, json=code).json()['detail'] == 'invalid_code'
    assert client.request('DELETE', PATH, json={}).json() == expected
    assert client.get('/api/auth/me').json() == original


def test_pending_digest_uses_latest_verified_recipient_and_reset(client):
    add(client)
    service = worker(lambda game, url: snapshot(url, price=200))
    service.check_due()
    code = request_code(client)
    assert client.put(PATH, json=code).status_code == 200
    service.deliver_due()
    assert service.mailer.messages[0]['To'] == 'alerts@example.com'
    assert emails()[0].state == 'sent'
    client.request('DELETE', PATH, json={})
    service.fetcher = lambda game, url: snapshot(url, price=300)
    service.clock = lambda: NOW + 86400
    service.check_due()
    service.deliver_due()
    assert service.mailer.messages[1]['To'] == 'alice@example.com'


def test_wrong_expired_and_cross_account_codes(client):
    code = request_code(client)
    wrong = {**code, 'code': '000000' if code['code'] != '000000' else '111111'}
    assert client.put(PATH, json=wrong).json()['detail'] == 'invalid_code'
    with app.state.database.session() as db:
        assert db.get(EmailChallenge, code['challenge_id']).attempts == 1
    token = client.headers['Authorization']
    login(client, 'bob')
    assert client.put(PATH, json=code).json()['detail'] == 'invalid_code'
    client.headers['Authorization'] = token
    with app.state.database.session() as db:
        db.get(EmailChallenge, code['challenge_id']).expires_at = int(time.time()) - 1
    assert client.put(PATH, json=code).json()['detail'] == 'code_expired'
    assert client.get(PATH).json()['email'] == 'alice@example.com'
