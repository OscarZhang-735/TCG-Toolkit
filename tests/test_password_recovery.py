from concurrent.futures import ThreadPoolExecutor
from threading import Event
import smtplib

import pytest

from auth_helpers import register_user
from test_email_verification import client, legacy_user, advance
from tcg_toolkit.auth.email_verification import AuthError, EmailVerification
from tcg_toolkit.auth.service import AuthService
from tcg_toolkit.web import app

NEW = {'new_password': 'new-password123', 'confirm_password': 'new-password123'}


def account(client, name='alice'):
    result = register_user(client, name).json()
    client.headers['Authorization'] = 'Bearer ' + result['token']
    # Registration and password recovery intentionally share the email quota.
    with app.state.auth.store.connect() as db:
        db.execute('UPDATE email_challenges SET created_at=created_at-61')
    return result


def code_body(client, mode, email='alice@example.com'):
    response = client.post(f'/api/auth/password/{mode}/email-code', json={'email': email, 'language': 'zh-CN'})
    assert response.status_code == 200, response.text
    return {**NEW, 'challenge_id': response.json()['challenge_id'],
            'code': app.state.auth.verification.mailer.messages[-1]['code'],
            **({'email': email} if mode == 'reset' else {'method': 'email'})}


def test_old_password_change_works_without_email_and_revokes_all_sessions(client):
    first = legacy_user()
    second = app.state.auth.login('legacy', 'password123')[1]
    other = legacy_user('other')
    client.headers['Authorization'] = 'Bearer ' + first
    app.state.auth.verification.secret = b''
    assert client.post('/api/auth/password/change', json={**NEW, 'method': 'password',
                       'current_password': 'wrongpassword'}).json()['detail'] == 'invalid_password'
    assert app.state.auth.current_user(first)
    assert client.post('/api/auth/password/change', json={**NEW, 'method': 'password',
                       'current_password': 'password123'}).status_code == 204
    assert app.state.auth.current_user(first) is None and app.state.auth.current_user(second) is None
    assert app.state.auth.current_user(other)
    assert app.state.auth.login('legacy', 'password123') is None
    assert app.state.auth.login('legacy', NEW['new_password'])


@pytest.mark.parametrize('mode', ['change', 'reset'])
def test_email_password_flow_persists_and_consumes_code(client, mode):
    original = account(client)
    body = code_body(client, mode)
    if mode == 'reset': client.headers.pop('Authorization')
    response = client.post(f'/api/auth/password/{mode}', json=body)
    assert response.status_code == 204 and response.headers['Cache-Control'] == 'no-store'
    assert app.state.auth.current_user(original['token']) is None
    assert app.state.auth.login('alice@example.com', 'password123') is None
    user, token = AuthService(app.state.auth.store).login('alice@example.com', NEW['new_password'])
    assert user['id'] == original['user']['id']
    if mode == 'change': client.headers['Authorization'] = 'Bearer ' + token
    assert client.post(f'/api/auth/password/{mode}', json=body).json()['detail'] == 'invalid_code'


def test_unknown_and_unverified_email_use_generic_success_without_mail(client):
    legacy_user()
    with app.state.auth.store.connect() as db:
        db.execute("UPDATE users SET email='unverified@example.com'")
    for email in ['unknown@example.com', 'unverified@example.com']:
        response = client.post('/api/auth/password/reset/email-code', json={'email': email})
        assert response.status_code == 200
        assert set(response.json()) == {'challenge_id','expires_in','retry_after'}
        assert client.post('/api/auth/password/reset', json={**NEW, 'email': email,
            'challenge_id': response.json()['challenge_id'], 'code': '123456'}).json()['detail'] == 'invalid_code'
    assert not app.state.auth.verification.mailer.messages


def test_change_email_requires_verified_account_and_ignores_client_target(client):
    token = legacy_user()
    client.headers['Authorization'] = 'Bearer ' + token
    assert client.post('/api/auth/password/change/email-code', json={}).json()['detail'] == 'verified_email_required'
    account(client)
    code_body(client, 'change', 'attacker@example.com')
    assert app.state.auth.verification.mailer.messages[-1]['email'] == 'alice@example.com'


def test_codes_cannot_cross_purposes_or_accounts(client, monkeypatch):
    alice = account(client)
    change = code_body(client, 'change')
    assert client.post('/api/auth/password/reset', json={**change, 'email':'alice@example.com'}).json()['detail'] == 'invalid_code'
    bob = account(client, 'bob')
    assert client.post('/api/auth/password/change', json=change).json()['detail'] == 'invalid_code'
    client.headers['Authorization'] = 'Bearer ' + alice['token']
    advance(monkeypatch, 61)
    reset = code_body(client, 'reset')
    assert client.post('/api/auth/password/change', json={k:v for k,v in {**reset, 'method':'email'}.items() if k!='email'}).json()['detail'] == 'invalid_code'
    assert client.post('/api/auth/password/reset', json={**reset, 'email':'bob@example.com'}).json()['detail'] == 'invalid_code'
    assert app.state.auth.current_user(bob['token'])


def test_failed_attempts_expiry_resend_and_smtp_failure(client, monkeypatch):
    account(client)
    body = code_body(client, 'reset')
    wrong = '000000' if body['code'] != '000000' else '111111'
    for _ in range(5):
        result = client.post('/api/auth/password/reset', json={**body,'code':wrong})
    assert result.json()['detail'] == 'code_attempts_exhausted'
    assert client.post('/api/auth/password/reset', json=body).json()['detail'] == 'code_attempts_exhausted'
    advance(monkeypatch, 61)
    fresh = code_body(client, 'reset')
    assert client.post('/api/auth/password/reset', json=body).json()['detail'] == 'invalid_code'
    advance(monkeypatch, 61)
    app.state.auth.verification.mailer.failure = smtplib.SMTPException('private SMTP error')
    failed = client.post('/api/auth/password/reset/email-code', json={'email':'alice@example.com'})
    assert failed.status_code == 503 and 'private' not in failed.text
    advance(monkeypatch, 600)
    assert client.post('/api/auth/password/reset', json=fresh).json()['detail'] == 'code_expired'


def test_old_password_change_invalidates_outstanding_reset_and_binding_proofs(client):
    original = account(client)
    body = code_body(client, 'reset')
    assert client.post('/api/auth/password/change', json={**NEW,'method':'password','current_password':'password123'}).status_code == 204
    assert client.post('/api/auth/password/reset', json=body).json()['detail'] == 'invalid_code'
    # Even if dependency authentication happened earlier, service rechecks the session under the lock.
    with pytest.raises(AuthError) as exc:
        app.state.auth.change_password(original['user']['id'], original['token'], 'anotherpassword',
                                       'password', NEW['new_password'])
    assert exc.value.status_code == 401


def test_inflight_email_cannot_reactivate_after_password_change(client):
    original = account(client)
    def delivery(*args):
        app.state.auth.change_password(original['user']['id'], original['token'], NEW['new_password'],
                                       'password', 'password123')
    app.state.auth.verification.mailer.send_code = delivery
    assert client.post('/api/auth/password/reset/email-code', json={'email':'alice@example.com'}).status_code == 503
    with app.state.auth.store.connect() as db:
        assert db.execute("SELECT count(*) FROM email_challenges WHERE state='active'").fetchone()[0] == 0


def test_concurrent_reset_succeeds_once(client):
    account(client)
    body = code_body(client, 'reset')
    def reset():
        try:
            app.state.auth.reset_password(body['email'],body['challenge_id'],body['code'],body['new_password'])
            return True
        except AuthError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: reset(), range(2))) == 1


def test_login_in_progress_cannot_leave_old_password_session_after_change(client, monkeypatch):
    import tcg_toolkit.auth.service as module
    original = account(client)
    verifying, release = Event(), Event()
    real_verify = module.verify_password
    def paused_verify(encoded, password):
        if password == 'password123':
            verifying.set()
            assert release.wait(5)
        return real_verify(encoded, password)
    monkeypatch.setattr(module, 'verify_password', paused_verify)
    with ThreadPoolExecutor(max_workers=2) as pool:
        login = pool.submit(app.state.auth.login, 'alice', 'password123')
        assert verifying.wait(5)
        change = pool.submit(app.state.auth.change_password, original['user']['id'], original['token'],
                             NEW['new_password'], 'password', 'password123')
        release.set()
        token = login.result()[1]
        change.result()
    assert app.state.auth.current_user(token) is None


@pytest.mark.parametrize('body', [
    {**NEW,'method':'password'},
    {**NEW,'method':'email'},
    {**NEW,'method':'password','current_password':'password123','code':'123456'},
    {**NEW,'method':'password','current_password':'password123','new_password':'short'},
    {**NEW,'method':'password','current_password':'password123','confirm_password':'mismatched'},
])
def test_validation_does_not_echo_passwords(client, body):
    account(client)
    response = client.post('/api/auth/password/change', json=body)
    assert response.status_code == 422 and response.json() == {'detail':'validation_error'}


@pytest.mark.parametrize('path,limit', [('change',10),('reset',10),('change/email-code',5),('reset/email-code',5)])
def test_password_guards_and_limits(client,path,limit):
    client.headers['Authorization'] = 'Bearer ' + legacy_user()
    url = '/api/auth/password/' + path
    assert client.post(url,json={},headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.post(url,content='{}',headers={'Content-Type':'text/plain'}).status_code == 415
    for _ in range(limit):
        assert client.post(url,json={}).status_code != 429
    response = client.post(url,json={})
    assert response.status_code == 429 and response.headers['Cache-Control'] == 'no-store'


def test_email_configuration_failure_does_not_change_password(client):
    original = account(client)
    app.state.auth.verification.secret = b''
    assert client.post('/api/auth/password/reset/email-code',json={'email':'alice@example.com'}).status_code == 503
    assert app.state.auth.current_user(original['token'])
    assert app.state.auth.login('alice','password123')
