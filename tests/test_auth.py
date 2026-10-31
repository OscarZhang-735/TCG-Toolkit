from auth_helpers import registration_body, register_user
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from tcg_toolkit.web import app
from tcg_toolkit.auth.service import AuthService, DuplicateUsername, token_hash
from tcg_toolkit.auth.store import UserStore

ORIGIN = "https://testserver"
BODY = {"username": "Alice", "password": "a long password!"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TCG_USER_DB", str(tmp_path / "users.sqlite3"))
    monkeypatch.setenv("TCG_TRUSTED_ORIGINS", ORIGIN)
    monkeypatch.setenv("TCG_COOKIE_SECURE", "true")
    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield client


def test_register_session_logout_login(client):
    response = register_user(client, **BODY)
    assert response.status_code == 201
    user = response.json()["user"]
    assert set(user) == {"id", "username", "created_at", "email", "email_verified"}
    assert user["username"] == "alice"
    assert "set-cookie" not in response.headers
    token = response.json()["token"]
    client.headers["Authorization"] = "Bearer " + token
    assert client.get("/api/auth/me").json() == user
    store = app.state.auth.store
    with store.connect() as db:
        encoded = db.execute("SELECT password_hash FROM users").fetchone()[0]
        digest = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
        assert encoded.startswith("$argon2id$") and BODY["password"] not in encoded
        assert digest == token_hash(token) and token != digest
        assert db.execute("PRAGMA user_version").fetchone()[0] == 8
    assert AuthService(UserStore(store.path)).current_user(token) == user
    assert client.post("/api/auth/logout", json={}).status_code == 204
    assert app.state.auth.current_user(token) is None
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={**BODY, "confirm_password": BODY["password"], "username": "ALICE"}).status_code == 200
    with store.connect() as db:
        db.execute("UPDATE sessions SET expires_at = 0")
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
