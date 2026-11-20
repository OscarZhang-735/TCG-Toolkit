from types import SimpleNamespace

import httpx
import pytest

from tcg_toolkit.currency import client as currency_module
from tcg_toolkit.external import CurrencyClient, CurrencyRequestError
from test_cards import client


def test_cache_refresh_and_restart(client, monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(currency_module, "time", SimpleNamespace(time=lambda: now[0]))
    calls = []
    def respond(request):
        assert request.headers["x-rapidapi-key"] == "test"
        assert request.url.params["from"] == "JPY"
        calls.append(request.url.params["to"])
        return httpx.Response(200, json={"result": 0.05 if len(calls) <= 2 else 0.06})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with CurrencyClient(api_key="test", client=http) as currency:
            assert currency.convert_jpy(100, "CNY")["result"] == 5
            assert currency.convert_jpy(200, "CAD")["result"] == 10
        assert calls == ["CNY", "CAD"]
        # The API uses the persisted rate after the original client closes.
        now[0] += 86400
        monkeypatch.setenv("RAPIDAPI_KEY", "")
        response = client.get("/api/currency/convert", params={"to": "CAD", "amount": 300})
        assert response.status_code == 200
        assert response.json()["result"] == 15
        assert len(calls) == 2
        now[0] += 1
        with CurrencyClient(api_key="test", client=http) as currency:
            assert currency.convert_jpy(100, "CNY")["result"] == 6
        assert calls == ["CNY", "CAD", "CNY", "CAD"]


def test_failures_consume_budget_and_preserve_success(postgres_schema, monkeypatch):
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
