import smtplib
import time

import pytest
from sqlalchemy import select

from tcg_toolkit.db_models import User, EmailChallenge
from tcg_toolkit.web import app
from tcg_toolkit.auth.email_verification import SMTPMailer
from test_subscriptions import client, add, worker, emails, snapshot, DAY, login

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
    service.check_daily(DAY)
    code = request_code(client)
    assert client.put(PATH, json=code).status_code == 200
    service.deliver_due()
    assert service.mailer.messages[0]['To'] == 'alerts@example.com'
    assert emails()[0].state == 'sent'
    client.request('DELETE', PATH, json={})
    from datetime import timedelta
    service.fetcher = lambda game, url: snapshot(url, price=300)
    service.check_daily(DAY + timedelta(days=1))
    service.deliver_due()
    assert service.mailer.messages[1]['To'] == 'alice@example.com'


def test_wrong_expired_and_cross_account_codes(client):
    code = request_code(client)
    wrong = {**code, 'code': '000000' if code['code'] != '000000' else '111111'}
    assert client.put(PATH, json=wrong).json()['detail'] == 'invalid_code'
    with app.state.auth.store.session() as db:
        assert db.get(EmailChallenge, code['challenge_id']).attempts == 1
    token = client.headers['Authorization']
    login(client, 'bob')
    assert client.put(PATH, json=code).json()['detail'] == 'invalid_code'
    client.headers['Authorization'] = token
    with app.state.auth.store.session() as db:
        db.get(EmailChallenge, code['challenge_id']).expires_at = int(time.time()) - 1
    assert client.put(PATH, json=code).json()['detail'] == 'code_expired'
    assert client.get(PATH).json()['email'] == 'alice@example.com'


def test_reset_invalidates_pending_codes_and_confirmation_supersedes_older_targets(client):
    first = request_code(client)
    second = request_code(client, 'another@example.com')
    assert client.put(PATH, json=second).status_code == 200
    assert client.put(PATH, json=first).json()['detail'] == 'invalid_code'
    third = request_code(client, 'third@example.com')
    client.request('DELETE', PATH, json={})
    assert client.put(PATH, json=third).json()['detail'] == 'invalid_code'


def test_delivery_failure_keeps_previous_address_and_rate_limits(client):
    first = request_code(client)
    client.put(PATH, json=first)
    assert client.post(PATH + '/code', json={'email': 'alerts@example.com'}).status_code == 429
    app.state.auth.verification.mailer.failure = smtplib.SMTPException('offline')
    response = client.post(PATH + '/code', json={'email': 'next@example.com'})
    assert response.status_code == 503
    assert client.get(PATH).json()['email'] == 'alerts@example.com'


def test_notification_proof_cannot_be_used_for_password_change(client):
    code = request_code(client)
    with app.state.auth.store.session() as db:
        account = db.scalar(select(User).where(User.username == 'alice'))
        challenge, error = app.state.auth.verification.check(db, **code, purpose='password_change', user_id=account.id)
        assert error.detail == 'invalid_code'


def test_verified_custom_address_allows_legacy_account_to_subscribe(client):
    with app.state.auth.store.session() as db:
        account = db.scalar(select(User).where(User.username == 'alice'))
        account.email = account.email_verified_at = None
    assert client.get(PATH).json()['email'] is None
    code = request_code(client)
    assert client.put(PATH, json=code).status_code == 200
    assert add(client)['id']


def test_reset_during_smtp_does_not_reactivate_challenge(client, monkeypatch):
    mailbox = app.state.auth.verification.mailer
    original = mailbox.send_code
    def send(*args):
        original(*args)
        assert client.request('DELETE', PATH, json={}).status_code == 200
    monkeypatch.setattr(mailbox, 'send_code', send)
    response = client.post(PATH + '/code', json={'email': 'alerts@example.com'})
    assert response.status_code == 503
    assert client.get(PATH).json()['using_registration_email'] is True
    with app.state.auth.store.session() as db:
        challenge = db.scalar(select(EmailChallenge).where(EmailChallenge.purpose == 'notification_email'))
        assert challenge.state != 'active'


@pytest.mark.parametrize('language,phrase', [('zh-CN', '订阅通知邮箱'), ('en', 'subscription notification email')])
def test_notification_verification_template(monkeypatch, language, phrase):
    messages = []
    monkeypatch.setattr(SMTPMailer, 'send_message', lambda self, message: messages.append(message))
    SMTPMailer().send_code('alerts@example.com', '123456', language, 'notification_email')
    assert phrase in messages[0].get_content()
    assert '123456' in messages[0].get_content()


@pytest.mark.parametrize('method,path,body', [('get',PATH,None), ('post',PATH+'/code',{'email':'a@example.com'}),
                                            ('put',PATH,{'challenge_id':'x','code':'123456'}), ('delete',PATH,{})])
def test_guards_and_cache(client, method, path, body):
    if method != 'get':
        assert client.request(method,path,json=body,headers={'Origin':'https://evil.example'}).status_code == 403
    client.headers.pop('Authorization')
    response = client.request(method,path,json=body)
    assert response.status_code == 401
    assert response.headers['Cache-Control'] == 'no-store'
