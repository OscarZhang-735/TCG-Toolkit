"""JPY conversion through RapidAPI's Currency Conversion and Exchange Rates."""

import math
import os
import time
from enum import Enum
from pathlib import Path

from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout
from ..database import Database

import httpx
from dotenv import dotenv_values


class TargetCurrency(str, Enum):
    CNY = "CNY"
    CAD = "CAD"


class CurrencyConfigError(RuntimeError):
    """The RapidAPI key is missing."""


class CurrencyRequestError(RuntimeError):
    """The provider request failed or returned an invalid conversion."""


class CurrencyClient:
    host = "currency-conversion-and-exchange-rates.p.rapidapi.com"

    def __init__(self, *, api_key: str | None = None, client: httpx.Client | None = None,
                 timeout: float = 30.0, store: Database | None = None):
        # Read the working directory's .env without modifying process variables.
        if api_key is None:
            api_key = os.environ.get("RAPIDAPI_KEY")
            if api_key is None:
                api_key = dotenv_values(Path.cwd() / ".env").get("RAPIDAPI_KEY")
        self._api_key = (api_key or "").strip()
        self._store = store
        self._owns_store = store is None
        self.timeout = timeout
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self._owns_store and self._store is not None:
            self._store.close()
        if self._owns_client:
            self._client.close()

    def convert_jpy(self, amount: float, to: TargetCurrency | str) -> dict:
        """Convert JPY using persisted CNY/CAD rates, refreshed after 24 hours."""
        target = TargetCurrency(to)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
            raise ValueError("amount must be a finite, non-negative number")
        errors = {}
        try:
            if self._store is None:
                self._store = Database()
            for currency in TargetCurrency:
                with self._store.connect() as db:
                    now = time.time()
                    if not self._api_key:
                        errors[currency] = CurrencyConfigError("RAPIDAPI_KEY is not configured.")
                        continue
                    # Commit the reservation before network I/O, including failed
                    # attempts. ON CONFLICT also serializes an initially empty cache.
                    reserved = db.execute("""INSERT INTO rates (currency, attempted_at) VALUES (%s, %s)
                        ON CONFLICT(currency) DO UPDATE SET attempted_at = excluded.attempted_at
                        WHERE (rates.fetched_at IS NULL OR rates.fetched_at < %s)
                          AND (rates.attempted_at IS NULL OR rates.attempted_at < %s)
                        RETURNING currency""", (currency.value, now, now - 86400, now - 86400)).fetchone()
                if not reserved:
                    continue
                try:
                    rate = self._fetch_rate(currency)
                except CurrencyRequestError as exc:
                    errors[currency] = exc
                    continue
                with self._store.connect() as db:
                    db.execute("UPDATE rates SET rate=%s, fetched_at=%s WHERE currency=%s",
                               (rate, time.time(), currency.value))
            with self._store.connect() as db:
                row = db.execute("SELECT rate, fetched_at FROM rates WHERE currency=%s",
                                 (target.value,)).fetchone()
        except (DatabaseError, PoolTimeout) as exc:
            raise CurrencyRequestError("Could not access the currency cache.") from exc
        if row is None or row["fetched_at"] is None or time.time() - row["fetched_at"] > 86400:
            if target in errors:
                raise errors[target]
            raise CurrencyRequestError("No fresh rate available; the next attempt is allowed after 24 hours.")
        return {"from": "JPY", "to": target.value, "amount": amount, "result": amount * row["rate"], "fetched_at": row["fetched_at"]}

    def _fetch_rate(self, target: TargetCurrency) -> float:
        try:
            response = self._client.get(
                f"https://{self.host}/convert",
                params={"from": "JPY", "to": target.value, "amount": 1},
                headers={"x-rapidapi-host": self.host, "x-rapidapi-key": self._api_key},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CurrencyRequestError("Could not retrieve the currency conversion.") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise CurrencyRequestError("Invalid currency conversion response.") from exc
        if not isinstance(data, dict) or data.get("success") is False:
            raise CurrencyRequestError("Currency conversion was unsuccessful.")
        result = data.get("result")
        if isinstance(result, bool) or not isinstance(result, (int, float)) or not math.isfinite(result) or result <= 0:
            raise CurrencyRequestError("Invalid currency conversion result.")
        return result
