"""JPY conversion through RapidAPI's Currency Conversion and Exchange Rates."""

import math
import os
import time
from enum import Enum
from pathlib import Path

from sqlalchemy import and_, or_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from ..db_models import CurrencyRate
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
                with self._store.session() as db:
                    now = time.time()
                    if not self._api_key:
                        errors[currency] = CurrencyConfigError("RAPIDAPI_KEY is not configured.")
                        continue
                    # Commit the reservation before network I/O, including failed
                    # attempts. ON CONFLICT also serializes an initially empty cache.
                    statement = insert(CurrencyRate).values(currency=currency.value, attempted_at=now)
                    reserved = db.scalar(statement.on_conflict_do_update(
                        index_elements=[CurrencyRate.currency],
                        set_={CurrencyRate.attempted_at: statement.excluded.attempted_at},
                        where=and_(
                            or_(CurrencyRate.fetched_at.is_(None), CurrencyRate.fetched_at < now - 86400),
                            or_(CurrencyRate.attempted_at.is_(None), CurrencyRate.attempted_at < now - 86400),
                        ),
                    ).returning(CurrencyRate.currency))
                if not reserved:
                    continue
                try:
                    rate = self._fetch_rate(currency)
                except CurrencyRequestError as exc:
                    errors[currency] = exc
                    continue
                with self._store.session() as db:
                    db.execute(update(CurrencyRate).where(CurrencyRate.currency == currency.value)
                               .values(rate=rate, fetched_at=time.time()))
            with self._store.session() as db:
                row = db.get(CurrencyRate, target.value)
        except SQLAlchemyError as exc:
            raise CurrencyRequestError("Could not access the currency cache.") from exc
        if row is None or row.fetched_at is None or time.time() - row.fetched_at > 86400:
            if target in errors:
                raise errors[target]
            raise CurrencyRequestError("No fresh rate available; the next attempt is allowed after 24 hours.")
        return {"from": "JPY", "to": target.value, "amount": amount, "result": amount * row.rate, "fetched_at": row.fetched_at}

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
