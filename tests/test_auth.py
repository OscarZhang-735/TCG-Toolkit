from io import BytesIO
from time import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select, update

from auth_helpers import register_user
from cos_bucket import BUCKET, cos, signed_object_url
from tcg_toolkit.auth.service import token_hash
from tcg_toolkit.media.objects import cleanup_image
from tcg_toolkit.persistence.models import AvatarCleanup, LoginSession, User
from tcg_toolkit.web import app

ORIGIN = "https://testserver"
BODY = {"username": "Alice", "password": "a long password!"}


@pytest.fixture
def client(monkeypatch, postgres_schema):
    monkeypatch.setenv("TCG_TRUSTED_ORIGINS", ORIGIN)
    monkeypatch.setenv("TCG_COOKIE_SECURE", "true")
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield client


def test_register_session_logout_login(client):
    response = register_user(client, **BODY)
    assert response.status_code == 201
    user = response.json()["user"]
    assert user["username"] == "alice"
    assert "set-cookie" not in response.headers
    token = response.json()["token"]
    client.headers["Authorization"] = "Bearer " + token
    assert client.get("/api/auth/me").json() == user
    store = app.state.database
    with store.session() as db:
        encoded = db.scalar(select(User.password_hash))
        digest = db.scalar(select(LoginSession.token_hash))
        assert encoded.startswith("$argon2id$") and BODY["password"] not in encoded
        assert digest == token_hash(token) and token != digest
    assert client.post("/api/auth/logout", json={}).status_code == 204
    assert app.state.auth.current_user(token) is None
    assert client.get("/api/auth/me").status_code == 401
    login = client.post("/api/auth/login", json=BODY)
    assert login.status_code == 200
    client.headers["Authorization"] = "Bearer " + login.json()["token"]
    assert client.get("/api/auth/me").json() == user
    with store.session() as db:
        db.execute(update(LoginSession).values(expires_at=0))
    assert client.get("/api/auth/me").status_code == 401


def test_new_login_replaces_other_device_and_stale_logout_is_harmless(client):
    first = register_user(client, **BODY).json()["token"]
    other = register_user(client, "bob").json()["token"]
    # A different device does not send the first device's Authorization header.
    response = client.post("/api/auth/login", json=BODY)
    assert response.status_code == 200
    latest = response.json()["token"]
    assert latest != first
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + first}).status_code == 401
    assert client.post("/api/auth/logout", json={}, headers={"Authorization": "Bearer " + first}).status_code == 204
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + latest}).status_code == 200
    assert app.state.auth.current_user(other)
    with app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(LoginSession).where(
            LoginSession.user_id == response.json()["user"]["id"])) == 1


def test_private_avatar_reads_and_cleanup_retry(client, cos):
    registered = register_user(client, **BODY).json()
    client.headers['Authorization'] = 'Bearer ' + registered['token']
    store = app.state.database
    user_id = registered['user']['id']
    # Existing records store the original object URL, without a signature.
    original = BUCKET + '/tcgtoolkit/avatars/' + 'a' * 32 + '.webp'
    with store.session() as db:
        db.get(User, user_id).avatar_url = original
    assert signed_object_url(client.get('/api/auth/me').json()['avatar_url']) == original

    cos.deny_delete = True
    photo = BytesIO()
    Image.new('RGB', (32, 32)).save(photo, format='PNG')
    response = client.put('/api/auth/avatar', content=photo.getvalue(), headers={'Content-Type': 'image/png'})
    assert response.status_code == 200
    replacement = signed_object_url(response.json()['avatar_url'])
    assert replacement in cos.objects and cos.deleted == [original]
    with store.session() as db:
        assert db.get(User, user_id).avatar_url == replacement
        pending = db.get(AvatarCleanup, original)
        assert pending.next_attempt_at > time()
        pending.next_attempt_at = 0

    cos.deny_delete = False
    # The object is already absent. COS returns 404 and the retry clears the queue.
    assert cleanup_image(store, original)
    assert cos.deleted == [original, original]
    with store.session() as db:
        assert db.get(AvatarCleanup, original) is None


def test_validation_and_errors(client):
    assert register_user(client, **BODY).status_code == 201
    assert register_user(client, "ALICE", BODY["password"], "another@example.com").status_code == 409
    for username in ("Alice", "unknown"):
        response = client.post("/api/auth/login", json={"username": username, "password": "wrong password!"})
        assert response.status_code == 401 and response.json()["detail"] == "invalid_credentials"
    assert client.post("/api/auth/register", json={**BODY, "username": "bad name"}).status_code == 422
    assert client.post("/api/auth/register", json={**BODY, "password": "short"}).status_code == 422


def test_write_guard(client):
    for endpoint in ("register", "login", "logout"):
        assert client.post(f"/api/auth/{endpoint}", json={**BODY, "confirm_password": BODY["password"]}, headers={"Origin": "https://evil.test"}).status_code == 403
        assert client.post(f"/api/auth/{endpoint}", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    client.headers.pop("origin")
    assert client.post('/api/auth/register', json=BODY).status_code == 403


def test_login_rate_limit_ignores_spoofed_ip(client, monkeypatch):
    import tcg_toolkit.auth.runtime as runtime
    now = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    for i in range(10):
        response = client.post("/api/auth/login", json={}, headers={"X-Forwarded-For": f"1.2.3.{i}"})
        assert response.status_code == 422
    assert client.post("/api/auth/login", json={}).status_code == 429
    now[0] += 61
    assert client.post("/api/auth/login", json={}).status_code == 422
