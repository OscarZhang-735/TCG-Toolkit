import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from auth_helpers import install_mailbox, registration_body
from tcg_toolkit.auth.passwords import hasher
from tcg_toolkit.persistence.models import EmailChallenge, User
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'


@pytest.fixture
def client(monkeypatch, postgres_schema):
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        install_mailbox()
        yield client


def legacy_user(username='legacy'):
    with app.state.database.session() as db:
        db.add(User(id=username, username=username, password_hash=hasher.hash('password123'), created_at=1))
    return app.state.auth.login(username, 'password123')[1]


def binding_body(client, email='bound@example.com'):
    response = client.post('/api/auth/email/bind-code', json={
        'email': email, 'current_password': 'password123', 'language': 'zh-CN'})
    assert response.status_code == 200, response.text
    return {'challenge_id': response.json()['challenge_id'],
            'code': app.state.auth.verification.mailer.messages[-1]['code']}


def advance(monkeypatch, seconds):
    import tcg_toolkit.auth.email_verification as module
    now = module.time.time()
    monkeypatch.setattr(module.time, 'time', lambda: now + seconds)


def test_verified_registration_normalizes_email_and_both_logins(client):
    body = registration_body(client, email=' Alice+Cards@EXAMPLE.COM ')
    with app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        row = db.scalar(select(EmailChallenge))
        assert len(row.code_hash) == 64 and row.code_hash != body['code']
    result = client.post('/api/auth/register', json=body)
    assert result.status_code == 201
    assert result.json()['user']['email'] == 'alice+cards@example.com'
    assert result.json()['user']['email_verified'] is True
    for name in ['ALICE', ' Alice+Cards@EXAMPLE.COM ']:
        login = client.post('/api/auth/login', json={'username': name, 'password': 'password123'})
        assert login.status_code == 200
    duplicate = client.post('/api/auth/register/email-code', json={'email': 'ALICE+CARDS@example.com'})
    assert duplicate.status_code == 409 and duplicate.json()['detail'] == 'email_taken'
    assert client.post('/api/auth/register', json=body).json()['detail'] == 'invalid_code'


def test_bad_attempts_are_persisted_and_exhaustion_blocks_correct_code(client):
    body = registration_body(client)
    wrong = '000000' if body['code'] != '000000' else '111111'
    for i in range(5):
        response = client.post('/api/auth/register', json={**body, 'code': wrong})
        assert response.json()['detail'] == ('code_attempts_exhausted' if i == 4 else 'invalid_code')
    assert client.post('/api/auth/register', json=body).json()['detail'] == 'code_attempts_exhausted'
    with app.state.database.session() as db:
        assert db.scalar(select(EmailChallenge.attempts)) == 5
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_expiration_and_resend(client, monkeypatch):
    body = registration_body(client)
    advance(monkeypatch, 600)
    assert client.post('/api/auth/register', json=body).json()['detail'] == 'code_expired'
    new = registration_body(client)
    assert client.post('/api/auth/register', json=body).json()['detail'] == 'invalid_code'
    assert client.post('/api/auth/register', json=new).status_code == 201


def test_legacy_binding_requires_password_and_updates_existing_session(client):
    token = legacy_user()
    client.headers['Authorization'] = 'Bearer ' + token
    assert client.get('/api/auth/me').json()['email'] is None
    response = client.post('/api/auth/email/bind-code', json={
        'email': 'bound@example.com', 'current_password': 'wrongpassword'})
    assert response.json()['detail'] == 'invalid_password'
    body = binding_body(client)
    assert app.state.auth.verification.mailer.messages[-1]['language'] == 'zh-CN'
    response = client.post('/api/auth/email/bind', json=body)
    assert response.status_code == 200 and response.json()['email_verified'] is True
    assert client.get('/api/auth/me').json()['email'] == 'bound@example.com'
    assert client.post('/api/auth/login', json={'username': 'BOUND@example.com', 'password': 'password123'}).status_code == 200
    # Login revokes the preceding bearer; authenticate with the new session before retrying.
    token = app.state.auth.login('legacy', 'password123')[1]
    client.headers['Authorization'] = 'Bearer ' + token
    assert client.post('/api/auth/email/bind', json=body).json()['detail'] == 'invalid_code'
    assert client.post('/api/auth/email/bind-code', json={
        'email': 'new@example.com', 'current_password': 'password123'}).json()['detail'] == 'email_already_bound'
