from sqlalchemy import select, func, update
from tcg_toolkit.db_models import AvatarCleanup, FavoriteGroup, LoginSession, SchemaMigration, User
from auth_helpers import registration_body, register_user
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import httpx
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from tcg_toolkit.web import app
from tcg_toolkit.auth.service import AuthService, DuplicateUsername, token_hash
from tcg_toolkit.auth.avatar import cleanup_avatar
from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.db_cli import SCHEMA_VERSION

ORIGIN = "https://testserver"
BODY = {"username": "Alice", "password": "a long password!"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TCG_TRUSTED_ORIGINS", ORIGIN)
    monkeypatch.setenv("TCG_COOKIE_SECURE", "true")
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield client


def test_register_session_logout_login(client):
    response = register_user(client, **BODY)
    assert response.status_code == 201
    user = response.json()["user"]
    assert set(user) == {"id", "username", "created_at", "email", "email_verified", "avatar_url"}
    assert user["avatar_url"] is None
    assert user["username"] == "alice"
    assert "set-cookie" not in response.headers
    token = response.json()["token"]
    client.headers["Authorization"] = "Bearer " + token
    assert client.get("/api/auth/me").json() == user
    store = app.state.auth.store
    with store.session() as db:
        encoded = db.scalar(select(User.password_hash))
        digest = db.scalar(select(LoginSession.token_hash))
        assert encoded.startswith("$argon2id$") and BODY["password"] not in encoded
        assert digest == token_hash(token) and token != digest
        assert db.scalar(select(func.max(SchemaMigration.version))) == SCHEMA_VERSION
        assert db.scalars(select(FavoriteGroup).where(FavoriteGroup.user_id == user['id'])).all() == []
    assert AuthService(UserStore(store.conninfo)).current_user(token) == user
    assert client.post("/api/auth/logout", json={}).status_code == 204
    assert app.state.auth.current_user(token) is None
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={**BODY, "confirm_password": BODY["password"], "username": "ALICE"}).status_code == 200
    with store.session() as db:
        db.execute(update(LoginSession).values(expires_at=0))
    assert client.get("/api/auth/me").status_code == 401


def test_validation_and_errors(client):
    assert register_user(client, **BODY).status_code == 201
    assert register_user(client, "ALICE", BODY["password"], "another@example.com").status_code == 409
    for username in ("Alice", "unknown"):
        response = client.post("/api/auth/login", json={"username": username, "password": "wrong password!"})
        assert response.status_code == 401 and response.json()["detail"] == "invalid_credentials"
    assert client.post("/api/auth/register", json={**BODY, "username": "bad name"}).status_code == 422
    assert client.post("/api/auth/register", json={**BODY, "password": "short"}).status_code == 422


def test_browser_assets_revalidate_after_upgrade(client):
    for path in ('/', '/auth.js', '/api.js', '/auth-client.js', '/app.js'):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'no-cache'
        cached = client.get(path, headers={'If-None-Match': response.headers['etag']})
        assert cached.status_code == 304
        assert cached.headers['Cache-Control'] == 'no-cache'


def test_anonymous_cannot_call_upstream(client, monkeypatch):
    from tcg_toolkit.web import SEARCHERS
    from tcg_toolkit.external import CurrencyClient
    def forbidden(*args, **kwargs):
        pytest.fail("anonymous request reached upstream")
    for key in SEARCHERS:
        monkeypatch.setitem(SEARCHERS, key, forbidden)
    monkeypatch.setattr(CurrencyClient, "convert_jpy", forbidden)
    for url in ("/api/search?game=wsr&keyword=x", "/api/currency/convert?to=CNY"):
        assert client.get(url).status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.get("/auth.js").status_code == 200
    for url in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(url).status_code == 404


def test_write_guard(client):
    for endpoint in ("register", "login", "logout"):
        assert client.post(f"/api/auth/{endpoint}", json={**BODY, "confirm_password": BODY["password"]}, headers={"Origin": "https://evil.test"}).status_code == 403
        assert client.post(f"/api/auth/{endpoint}", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    client.headers.pop("origin")
    assert client.post('/api/auth/register', json=BODY).status_code == 403


@pytest.mark.parametrize("action,limit", [("login", 10), ("register", 10), ("register/email-code", 5)])
def test_rate_limit_and_spoofed_ip(client, action, limit, monkeypatch):
    import tcg_toolkit.auth.routes as routes
    now = [100.0]
    monkeypatch.setattr(routes.time, "monotonic", lambda: now[0])
    for i in range(limit):
        response = client.post(f"/api/auth/{action}", json={}, headers={"X-Forwarded-For": f"1.2.3.{i}"})
        assert response.status_code == 422
    assert client.post(f"/api/auth/{action}", json={}).status_code == 429
    now[0] += 61
    assert client.post(f"/api/auth/{action}", json={}).status_code == 422


def test_concurrent_registration(client):
    bodies = [registration_body(client, **BODY, email=f"concurrent{i}@example.com") for i in range(2)]
    def register(body):
        try:
            return app.state.auth.register(**{k:v for k,v in body.items() if k != "confirm_password"})[0]
        except DuplicateUsername:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, bodies))
    assert sum(result is not None for result in results) == 1


def test_restart_keeps_session(client):
    register_user(client, **BODY)
    token = app.state.auth.login(BODY["username"], BODY["password"])[1]
    with TestClient(app, base_url=ORIGIN) as restarted:
        restarted.headers["Authorization"] = "Bearer " + token
        assert restarted.get("/api/auth/me").json()["username"] == "alice"


@pytest.mark.parametrize("length,expected", [(7,422),(8,201),(18,201),(19,422)])
def test_password_lengths(client, length, expected):
    password = "x" * length
    assert register_user(client, "length_test", password).status_code == expected
    if expected == 201:
        assert client.post("/api/auth/login", json={"username":"length_test", "password":password}).status_code == 200


def test_registration_confirmation(client):
    assert client.post("/api/auth/register", json=BODY).status_code == 422
    assert client.post("/api/auth/register", json={**BODY, "confirm_password":"different-password"}).status_code == 422


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_cors(client, method):
    headers = {"Origin": ORIGIN, "Access-Control-Request-Method": method,
               "Access-Control-Request-Headers": "authorization,content-type"}
    response = client.options("/api/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in response.headers
    response = client.options("/api/auth/me", headers={**headers, "Origin": "https://evil.test"})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cookie_ignored_and_malformed_bearer(client):
    token = register_user(client, **BODY).json()["token"]
    client.cookies.set("tcg_session", token)
    assert client.get("/api/auth/me").status_code == 401
    for value in ("Basic " + token, "Bearer", "Bearer wrong", "Bearer " + token + " extra"):
        assert client.get("/api/auth/me", headers={"Authorization": value}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 200


def test_build_version(client, monkeypatch):
    monkeypatch.setenv("TCG_BUILD_SHA", "a" * 40)
    assert client.get("/api/version").json() == {"sha": "a" * 40}


def test_avatar_upload_persists_and_failed_replacement_preserves_it(client, monkeypatch):
    monkeypatch.setenv('TCG_AVATAR_COS_URL', 'https://avatars.example.com/')
    registered = register_user(client, **BODY).json()
    client.headers['Authorization'] = 'Bearer ' + registered['token']
    image = BytesIO()
    Image.new('RGBA', (512, 256), 'red').save(image, format='PNG')
    uploaded = []

    def put(url, *, content, headers, **options):
        assert url.startswith('https://avatars.example.com/tcgtoolkit/avatars/')
        assert url.endswith('.webp')
        assert headers['Content-Type'] == 'image/webp'
        with Image.open(BytesIO(content)) as avatar:
            assert avatar.format == 'WEBP' and avatar.size == (256, 128)
        uploaded.append(url)
        return httpx.Response(200 if len(uploaded) == 1 else 403, request=httpx.Request('PUT', url))

    monkeypatch.setattr('tcg_toolkit.auth.avatar.httpx.put', put)
    monkeypatch.setattr('tcg_toolkit.auth.avatar.httpx.delete', lambda *args, **kwargs: pytest.fail('Avatar was deleted'))
    headers = {'Content-Type': 'image/png'}
    response = client.put('/api/auth/avatar', content=image.getvalue(), headers=headers)
    assert response.status_code == 200
    assert response.json() == {'avatar_url': uploaded[0]}
    assert client.get('/api/auth/me').json()['avatar_url'] == uploaded[0]
    login = client.post('/api/auth/login', json=BODY)
    assert login.json()['user']['avatar_url'] == uploaded[0]
    client.headers['Authorization'] = 'Bearer ' + login.json()['token']
    response = client.put('/api/auth/avatar', content=image.getvalue(), headers=headers)
    assert response.status_code == 502
    assert uploaded[1] != uploaded[0]
    assert client.get('/api/auth/me').json()['avatar_url'] == uploaded[0]
    client.headers['Authorization'] = 'Bearer ' + register_user(client, 'bob').json()['token']
    assert client.get('/api/auth/me').json()['avatar_url'] is None


@pytest.mark.parametrize('first_delete_status', [204, 503])
def test_avatar_replacement_cleans_up_after_commit(client, monkeypatch, first_delete_status):
    monkeypatch.setenv('TCG_AVATAR_COS_URL', 'https://avatars.example.com')
    registered = register_user(client, **BODY).json()
    user_id = registered['user']['id']
    client.headers['Authorization'] = 'Bearer ' + registered['token']
    image = BytesIO()
    Image.new('RGB', (32, 32), 'red').save(image, format='PNG')
    store = app.state.auth.store
    uploads, deletes = [], []

    def put(url, **kwargs):
        uploads.append(url)
        return httpx.Response(200, request=httpx.Request('PUT', url))

    def delete(url, **kwargs):
        with store.session() as db:
            assert db.get(User, user_id).avatar_url == uploads[-1]
        deletes.append(url)
        return httpx.Response(first_delete_status if len(deletes) == 1 else 404,
                              request=httpx.Request('DELETE', url))

    monkeypatch.setattr('tcg_toolkit.auth.avatar.httpx.put', put)
    monkeypatch.setattr('tcg_toolkit.auth.avatar.httpx.delete', delete)
    for _ in range(2):
        response = client.put('/api/auth/avatar', content=image.getvalue(), headers={'Content-Type': 'image/png'})
        assert response.status_code == 200
    assert response.json()['avatar_url'] == uploads[1]
    assert deletes == [uploads[0]]
    if first_delete_status == 503:
        with store.session() as db:
            db.get(AvatarCleanup, uploads[0]).next_attempt_at = 0
        with UserStore(store.conninfo) as restarted_store:
            assert cleanup_avatar(restarted_store)
        assert deletes == [uploads[0], uploads[0]]
    with store.session() as db:
        assert db.scalars(select(AvatarCleanup)).all() == []
        assert db.get(User, user_id).avatar_url == uploads[1]


def test_avatar_rejects_untrusted_or_invalid_uploads_before_cos(client, monkeypatch):
    monkeypatch.setenv('TCG_AVATAR_COS_URL', 'https://avatars.example.com')
    def unexpected_upload(*args, **kwargs):
        pytest.fail('Invalid or unauthorized upload reached COS')

    monkeypatch.setattr('tcg_toolkit.auth.avatar.httpx.put', unexpected_upload)
    path = '/api/auth/avatar'
    headers = {'Content-Type': 'image/png'}
    assert client.put(path, content=b'invalid', headers=headers).status_code == 401
    client.headers['Authorization'] = 'Bearer ' + register_user(client, **BODY).json()['token']
    assert client.put(path, content=b'invalid', headers={**headers, 'Origin': 'https://evil.test'}).status_code == 403
    assert client.put(path, content=b'<svg/>', headers={'Content-Type': 'image/svg+xml'}).status_code == 415
    assert client.put(path, content=b'invalid', headers=headers).status_code == 422
    assert client.put(path, content=b'x' * (5 * 1024 * 1024 + 1), headers=headers).status_code == 413
    monkeypatch.delenv('TCG_AVATAR_COS_URL')
    response = client.put(path, content=b'invalid', headers=headers)
    assert response.status_code == 503 and response.json()['detail'] == 'avatar_storage_failed'
    assert client.get('/api/auth/me').json()['avatar_url'] is None
