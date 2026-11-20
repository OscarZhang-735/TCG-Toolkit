import pytest
from sqlalchemy import update

from auth_helpers import register_user
from test_email_verification import advance, client, legacy_user
from tcg_toolkit.auth.service import AuthService
from tcg_toolkit.persistence.models import EmailChallenge, User
from tcg_toolkit.web import app

NEW = {'new_password': 'new-password123', 'confirm_password': 'new-password123'}


def account(client, name='alice'):
    result = register_user(client, name).json()
    client.headers['Authorization'] = 'Bearer ' + result['token']
    # Registration and password recovery intentionally share the email quota.
    with app.state.database.session() as db:
        db.execute(update(EmailChallenge).values(created_at=EmailChallenge.created_at-61))
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
    assert app.state.auth.current_user(first) is None
    client.headers['Authorization'] = 'Bearer ' + second
    app.state.auth.verification.secret = b''
    assert client.post('/api/auth/password/change', json={**NEW, 'method': 'password',
                       'current_password': 'wrongpassword'}).json()['detail'] == 'invalid_password'
    assert app.state.auth.current_user(second)
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
    user, token = AuthService(app.state.database).login('alice@example.com', NEW['new_password'])
    assert user['id'] == original['user']['id']
    if mode == 'change': client.headers['Authorization'] = 'Bearer ' + token
    assert client.post(f'/api/auth/password/{mode}', json=body).json()['detail'] == 'invalid_code'


def test_unknown_and_unverified_email_use_generic_success_without_mail(client):
    legacy_user()
    with app.state.database.session() as db:
        db.execute(update(User).values(email="unverified@example.com"))
    for email in ['unknown@example.com', 'unverified@example.com']:
        response = client.post('/api/auth/password/reset/email-code', json={'email': email})
        assert response.status_code == 200
        assert set(response.json()) == {'challenge_id','expires_in','retry_after'}
        assert client.post('/api/auth/password/reset', json={**NEW, 'email': email,
            'challenge_id': response.json()['challenge_id'], 'code': '123456'}).json()['detail'] == 'invalid_code'
    assert not app.state.auth.verification.mailer.messages


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
