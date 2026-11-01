from sqlalchemy import select
from tcg_toolkit.db_models import CurrencyRate
from tcg_toolkit.database import Database
import httpx
import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from fastapi.testclient import TestClient

from tcg_toolkit.external import CurrencyClient, CurrencyConfigError, CurrencyRequestError, TargetCurrency
from tcg_toolkit.web import app
from tcg_toolkit.external import currency as currency_module


@pytest.fixture(autouse=True)
def authenticated_business_api():
    from tcg_toolkit.auth.routes import current_user
    app.dependency_overrides[current_user] = lambda: {"id": "test-user"}
    yield
    app.dependency_overrides.pop(current_user, None)




@pytest.mark.parametrize("target", list(TargetCurrency))
def test_presets(target):
    def respond(request):
        assert request.url.host == CurrencyClient.host
        assert request.url.path == "/convert"
        assert dict(request.url.params) == {"from": "JPY", "to": request.url.params["to"], "amount": "1"}
        assert request.headers["x-rapidapi-key"] == "test-key"
        assert request.headers["x-rapidapi-host"] == CurrencyClient.host
        return httpx.Response(200, json={"success": True, "result": 5.25})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with CurrencyClient(api_key="test-key", client=http) as currency:
            result = currency.convert_jpy(750, target)
            assert result.pop("fetched_at") > 0
            assert result == {"from": "JPY", "to": target.value, "amount": 750, "result": 3937.5}
        assert not http.is_closed


def test_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    with pytest.raises(CurrencyConfigError):
        with CurrencyClient() as currency:
            currency.convert_jpy(1, "CNY")
    (tmp_path / ".env").write_text("RAPIDAPI_KEY=local-key\n")
    with CurrencyClient() as currency:
        assert currency._api_key == "local-key"
    monkeypatch.setenv("RAPIDAPI_KEY", "environment-key")
    with CurrencyClient() as currency:
        assert currency._api_key == "environment-key"


@pytest.mark.parametrize("amount,target", [(-1, "CNY"), (float("nan"), "CNY"), (float("inf"), "CAD"), (True, "CNY"), (1, "EUR")])
def test_invalid_input(amount, target):
    with CurrencyClient(api_key="test-key") as currency, pytest.raises(ValueError):
        currency.convert_jpy(amount, target)


@pytest.mark.parametrize("response", [httpx.Response(403), httpx.Response(429), httpx.Response(500),
    httpx.Response(200, text="not json"), httpx.Response(200, json={"success": False}),
    httpx.Response(200, json={}), httpx.Response(200, json={"result": "bad"})])
def test_provider_errors(response):
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http:
        with CurrencyClient(api_key="test-key", client=http) as currency, pytest.raises(CurrencyRequestError):
            currency.convert_jpy(1, "CNY")


@pytest.mark.parametrize("target", ["CNY", "CAD"])
def test_web_conversion(target, monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "test-key")
    def convert(self, amount, to):
        assert amount == 750 and to == target
        return {"from": "JPY", "to": to, "amount": amount, "result": 5.25}
    monkeypatch.setattr(CurrencyClient, "convert_jpy", convert)
    response = TestClient(app).get("/api/currency/convert", params={"to": target, "amount": 750})
    assert response.status_code == 200
    assert response.json()["result"] == 5.25


@pytest.mark.parametrize("params", [{"to": "USD"}, {"to": "EUR"}, {"to": "CNY", "amount": -1},
    {"to": "CNY", "amount": "nan"}, {"to": "CAD", "amount": "inf"}, {}])
def test_web_validation(params):
    assert TestClient(app).get("/api/currency/convert", params=params).status_code == 422


@pytest.mark.parametrize("kind,status", [("config", 503), ("request", 502), ("timeout", 504)])
def test_web_errors(kind, status, monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY", "test-key")
    def convert(*args):
        if kind == "config":
            raise CurrencyConfigError("RAPIDAPI_KEY is not configured.")
        cause = httpx.ReadTimeout("private upstream detail") if kind == "timeout" else None
        raise CurrencyRequestError("private upstream detail") from cause
    monkeypatch.setattr(CurrencyClient, "convert_jpy", convert)
    response = TestClient(app).get("/api/currency/convert?to=CNY")
    assert response.status_code == status
    assert "private upstream detail" not in response.text


def test_cache_refresh_and_restart(monkeypatch, tmp_path):
    now = [1_000_000.0]
    monkeypatch.setattr(currency_module.time, "time", lambda: now[0])
    calls = []
    def respond(request):
        calls.append(request.url.params["to"])
        return httpx.Response(200, json={"result": 0.05 if len(calls) <= 2 else 0.06})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with CurrencyClient(api_key="test", client=http) as currency:
            assert currency.convert_jpy(100, "CNY")["result"] == 5
            assert currency.convert_jpy(200, "CAD")["result"] == 10
        assert calls == ["CNY", "CAD"]
        # A new client, even without a key, uses the persisted rates.
        now[0] += 86400
        with CurrencyClient(api_key="", client=http) as currency:
            assert currency.convert_jpy(300, "CAD")["result"] == 15
        assert len(calls) == 2
        now[0] += 1
        with CurrencyClient(api_key="test", client=http) as currency:
            assert currency.convert_jpy(100, "CNY")["result"] == 6
        assert calls == ["CNY", "CAD", "CNY", "CAD"]
    with Database() as store, store.session() as db:
        assert db.scalars(select(CurrencyRate.fetched_at)).all() == [now[0], now[0]]


def test_failures_consume_budget_and_preserve_success(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(currency_module.time, "time", lambda: now[0])
    calls = []
    def respond(request):
        calls.append(request.url.params["to"])
        if request.url.params["to"] == "CAD":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(200, json={"result": 0.05})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        for _ in range(3):
            with CurrencyClient(api_key="test", client=http) as currency:
                assert currency.convert_jpy(100, "CNY")["result"] == 5
                with pytest.raises(CurrencyRequestError):
                    currency.convert_jpy(100, "CAD")
        assert calls == ["CNY", "CAD"]
        now[0] += 86401
        with CurrencyClient(api_key="test", client=http) as currency:
            with pytest.raises(CurrencyRequestError) as error:
                currency.convert_jpy(100, "CAD")
            assert isinstance(error.value.__cause__, httpx.ReadTimeout)
        assert calls == ["CNY", "CAD", "CNY", "CAD"]


def test_concurrent_refresh_reserves_each_currency_once():
    started, release = Event(), Event()
    calls = []
    def respond(request):
        calls.append(request.url.params["to"])
        if request.url.params["to"] == "CNY":
            started.set()
            assert release.wait(5)
        return httpx.Response(200, json={"result": 0.05})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        def convert(target):
            with CurrencyClient(api_key="test", client=http) as currency:
                return currency.convert_jpy(100, target)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(convert, "CNY")
            try:
                assert started.wait(5)
                second = executor.submit(convert, "CAD")
                assert second.result(timeout=5)["result"] == 5
            finally:
                release.set()
            assert first.result(timeout=5)["result"] == 5
    assert calls == ["CNY", "CAD"]
