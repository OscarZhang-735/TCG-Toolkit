from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import smtplib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from auth_helpers import (Mailbox, TEST_SECRET, create_v6_database, install_mailbox,
                          registration_body, register_user)
from tcg_toolkit.auth.email_verification import AuthError, EmailVerification, SMTPMailer, normalize_email
from tcg_toolkit.auth.passwords import hasher
from tcg_toolkit.auth.service import AuthService, token_hash
from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.web import app

ORIGIN = 'https://testserver'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('TCG_USER_DB', str(tmp_path / 'users.sqlite3'))
    monkeypatch.setenv('TCG_TRUSTED_ORIGINS', ORIGIN)
    with TestClient(app, base_url=ORIGIN, headers={'Origin': ORIGIN}) as client:
        install_mailbox()
        yield client


def legacy_user(username='legacy'):
    with app.state.auth.store.connect() as db:
        db.execute('INSERT INTO users (id,username,password_hash,created_at) VALUES (?,?,?,?)',
                   (username, username, hasher.hash('password123'), 1))
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
    with app.state.auth.store.connect() as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0] == 0
        row = db.execute('SELECT * FROM email_challenges').fetchone()
        assert len(row['code_hash']) == 64 and row['code_hash'] != body['code']
    result = client.post('/api/auth/register', json=body)
    assert result.status_code == 201
    assert result.json()['user']['email'] == 'alice+cards@example.com'
    assert result.json()['user']['email_verified'] is True
    for name in ['ALICE', ' Alice+Cards@EXAMPLE.COM ']:
        login = client.post('/api/auth/login', json={'username': name, 'password': 'password123'})
        assert login.status_code == 200
    assert normalize_email('a.b+tag@example.com') == 'a.b+tag@example.com'
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
    with app.state.auth.store.connect() as db:
        assert db.execute('SELECT attempts FROM email_challenges').fetchone()[0] == 5
        assert db.execute('SELECT count(*) FROM users').fetchone()[0] == 0


def test_expiration_and_resend(client, monkeypatch):
    body = registration_body(client)
    advance(monkeypatch, 600)
    assert client.post('/api/auth/register', json=body).json()['detail'] == 'code_expired'
    new = registration_body(client)
    assert client.post('/api/auth/register', json=body).json()['detail'] == 'invalid_code'
    assert client.post('/api/auth/register', json=new).status_code == 201


def test_email_cooldown_hourly_quota_and_restart(client, monkeypatch):
    advance(monkeypatch, 0)  # Freeze before issuance; assertions must not cross a wall-clock second.
    body = registration_body(client)
    response = client.post('/api/auth/register/email-code', json={'email': body['email']})
    assert response.status_code == 429 and int(response.headers['Retry-After']) == 60
    verification = app.state.auth.verification
    app.state.auth = AuthService(UserStore(app.state.auth.store.path),
                                EmailVerification(app.state.auth.store, verification.mailer, TEST_SECRET))
    with pytest.raises(AuthError) as failure:
        app.state.auth.verification.send(body['email'])
    assert failure.value.status_code == 429
    for _ in range(4):
        advance(monkeypatch, 60)
        app.state.auth.verification.send(body['email'])
    advance(monkeypatch, 60)
    with pytest.raises(AuthError) as failure:
        app.state.auth.verification.send(body['email'])
    assert failure.value.headers['Retry-After'] == '3300'
    advance(monkeypatch, 3300)
    assert app.state.auth.verification.send(body['email'])['challenge_id']


def test_code_survives_service_restart(client):
    body = registration_body(client)
    app.state.auth = AuthService(UserStore(app.state.auth.store.path),
                                EmailVerification(app.state.auth.store, Mailbox(), TEST_SECRET))
    assert client.post('/api/auth/register', json=body).status_code == 201


def test_failed_send_preserves_previous_code_and_counts_quota(client, monkeypatch):
    body = registration_body(client)
    advance(monkeypatch, 60)
    app.state.auth.verification.mailer.failure = smtplib.SMTPException('secret SMTP details')
    response = client.post('/api/auth/register/email-code', json={'email': body['email']})
    assert response.status_code == 503 and response.json()['detail'] == 'email_send_failed'
    assert 'secret' not in response.text
    retry = client.post('/api/auth/register/email-code', json={'email': body['email']})
    assert retry.status_code == 429
    assert client.post('/api/auth/register', json=body).status_code == 201


def test_username_collision_does_not_consume_code(client):
    register_user(client, 'alice')
    body = registration_body(client, 'ALICE', email='second@example.com')
    assert client.post('/api/auth/register', json=body).json()['detail'] == 'username_taken'
    assert client.post('/api/auth/register', json={**body, 'username': 'bob'}).status_code == 201


def test_concurrent_verification_consumes_once(client):
    body = registration_body(client)
    args = {k: v for k, v in body.items() if k != 'confirm_password'}
    def register():
        try:
            return app.state.auth.register(**args)
        except AuthError as exc:
            return exc.detail
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: register(), range(2)))
    assert sum(isinstance(result, tuple) for result in results) == 1
    assert 'invalid_code' in results
    with app.state.auth.store.connect() as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 1


def test_concurrent_send_reserves_quota_once(client):
    def send():
        try:
            return app.state.auth.verification.send('concurrent@example.com')
        except AuthError as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 429 in results
    assert len(app.state.auth.verification.mailer.messages) == 1


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


def test_challenge_bound_to_email_purpose_and_user(client):
    registration = registration_body(client)
    assert client.post('/api/auth/register', json={**registration, 'email': 'other@example.com'}).json()['detail'] == 'invalid_code'
    alice, bob = legacy_user('alice'), legacy_user('bob')
    client.headers['Authorization'] = 'Bearer ' + alice
    assert client.post('/api/auth/email/bind', json={k: registration[k] for k in ['challenge_id','code']}).json()['detail'] == 'invalid_code'
    binding = binding_body(client)
    client.headers['Authorization'] = 'Bearer ' + bob
    assert client.post('/api/auth/email/bind', json=binding).json()['detail'] == 'invalid_code'
    assert client.post('/api/auth/register', json={**registration, **binding, 'email': 'bound@example.com'}).json()['detail'] == 'invalid_code'
    client.headers['Authorization'] = 'Bearer ' + alice
    assert client.post('/api/auth/email/bind', json=binding).status_code == 200


def test_email_conflict_between_binding_and_registration_keeps_code(client):
    body = registration_body(client)
    client.headers['Authorization'] = 'Bearer ' + legacy_user()
    # Separate purposes can compete for the same address after the shared cooldown.
    with app.state.auth.store.connect() as db:
        db.execute('UPDATE email_challenges SET created_at=created_at-60')
    binding = binding_body(client, body['email'])
    assert client.post('/api/auth/email/bind', json=binding).status_code == 200
    response = client.post('/api/auth/register', json=body)
    assert response.status_code == 409 and response.json()['detail'] == 'email_taken'
    with app.state.auth.store.connect() as db:
        assert db.execute('SELECT state FROM email_challenges WHERE id=?', (body['challenge_id'],)).fetchone()[0] == 'active'


@pytest.mark.parametrize('path', ['register/email-code', 'email/bind-code', 'email/bind', 'register'])
def test_guards_no_cache_and_no_sensitive_validation_echo(client, path):
    for origin, status in [('https://evil.example', 403), (ORIGIN, None)]:
        response = client.post('/api/auth/' + path, headers={'Origin': origin},
                               json={'password': 'private', 'code': 'secret-code'})
        assert response.status_code == status if status else response.status_code in (401, 422)
        assert 'secret-code' not in response.text and 'private' not in response.text
        assert response.headers['Cache-Control'] == 'no-store'
        if origin == ORIGIN:
            assert 'Retry-After' in response.headers['Access-Control-Expose-Headers']
    assert client.post('/api/auth/' + path, content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415


def test_configuration_missing_only_disables_email(client):
    token = legacy_user()
    app.state.auth.verification = EmailVerification(app.state.auth.store, Mailbox(), '')
    assert client.post('/api/auth/register/email-code', json={'email': 'alice@example.com'}).json()['detail'] == 'email_unavailable'
    assert client.post('/api/auth/login', json={'username': 'legacy', 'password': 'password123'}).status_code == 200
    assert client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + token}).status_code == 200


def test_v7_upgrade_preserves_old_accounts_and_sessions(tmp_path):
    path = tmp_path / 'v7.sqlite3'
    create_v6_database(path)
    with sqlite3.connect(path) as db:
        # v7 has the same account schema as v6 and no saved upstream credentials.
        db.execute('PRAGMA user_version=7')
        db.execute('INSERT INTO users VALUES (?,?,?,?)', ('old', 'olduser', hasher.hash('password123'), 1))
        db.execute('INSERT INTO sessions VALUES (?,?,?)', (token_hash('oldtoken'), 'old', 9999999999))
    service = AuthService(UserStore(path))
    assert service.current_user('oldtoken')['email'] is None
    assert service.login('OLDUSER', 'password123')[0]['email_verified'] is False
    with UserStore(path).connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 8


def test_overlapping_startup_cannot_reset_v8_to_v7(tmp_path):
    path = tmp_path / 'overlapping.sqlite3'
    create_v6_database(path)

    class OverlappingStore(UserStore):
        @contextmanager
        def connect(self):
            with super().connect() as db:
                class Connection:
                    def execute(self, sql, *args):
                        result = db.execute(sql, *args)
                        if sql == 'VACUUM':
                            # Another startup finishes while this one is between
                            # secret cleanup and the final schema-version write.
                            UserStore(path)
                        return result
                yield Connection()

    store = OverlappingStore(path)
    with store.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 8
        assert db.execute('SELECT count(*) FROM email_challenges').fetchone()[0] == 0


@pytest.mark.parametrize('mode,port', [('starttls', 587), ('ssl', 465)])
@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_smtp_transport_tls_and_templates(monkeypatch, mode, port, language):
    for key, value in {'HOST':'smtp.example.com', 'USERNAME':'sender', 'PASSWORD':'smtp-password',
                       'FROM':'TCG Toolkit <sender@example.com>', 'TLS':mode}.items():
        monkeypatch.setenv('TCG_SMTP_' + key, value)
    monkeypatch.delenv('TCG_SMTP_PORT', raising=False)
    calls = []
    class SMTP:
        def __init__(self, host, actual_port, **kwargs):
            assert host == 'smtp.example.com' and actual_port == port and kwargs['timeout'] == 10
            assert ('context' in kwargs) == (mode == 'ssl')
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def starttls(self, **kwargs):
            assert kwargs['context'].check_hostname
            calls.append('tls')
        def login(self, username, password):
            assert (username, password) == ('sender', 'smtp-password')
            calls.append('login')
        def send_message(self, message):
            assert message['To'] == 'alice@example.com'
            assert '012345' in message.get_content() and '10' in message.get_content()
            assert ('验证码' in message['Subject']) == (language == 'zh-CN')
            calls.append('send')
            return {}
    monkeypatch.setattr(smtplib, 'SMTP_SSL' if mode == 'ssl' else 'SMTP', SMTP)
    mailer = SMTPMailer()
    assert mailer.configured
    mailer.send_code('alice@example.com', '012345', language, 'register')
    assert calls == (['tls'] if mode == 'starttls' else []) + ['login', 'send']
