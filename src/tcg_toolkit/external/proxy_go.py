"""Explicit proxy-go forwarding for targets that require challenge handling."""

import base64
import os
import re
from pathlib import Path

import httpx
from dotenv import dotenv_values


class ProxyGoConfigError(ValueError):
    """The proxy-go endpoint or API key is missing or invalid."""


def _header_int(headers: httpx.Headers, name: str) -> int | None:
    value = headers.get(name)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


class ProxyGoGatewayError(httpx.TransportError):
    """Gateway failure, distinct from the target site's HTTP status."""

    def __init__(self, response: httpx.Response, *, request: httpx.Request):
        super().__init__(f"proxy-go gateway HTTP {response.status_code}", request=request)
        self.status_code = response.status_code
        try:
            data = response.json()
        except ValueError:
            data = None
        self.detail = data.get("detail") if isinstance(data, dict) else None
        self.request_id = response.headers.get("X-Request-ID")
        self.proxy_total_bytes = _header_int(response.headers, "X-Proxy-Total-Bytes")
        self.proxy_attempts = _header_int(response.headers, "X-Proxy-Attempts")
        self.headers = response.headers


class ProxyGoClient:
    """Fetch GET/HEAD responses through proxy-go without changing business routing.

    Returned responses use the target's status, headers and decoded body. Gateway
    diagnostics live in ``response.extensions['proxy_go']``. Redirects and target
    HTTP errors are left to the caller; challenge retries belong to the gateway.
    """

    name = "proxy-go"

    def __init__(self, *, api_url: str | None = None, api_key: str | None = None,
                 transport: httpx.BaseTransport | None = None,
                 timeout: float | httpx.Timeout = httpx.Timeout(220, connect=5)):
        # Match other standalone external clients: read .env without mutating it
        # or the process environment. An explicitly empty setting stays empty.
        config = None

        def setting(value, name):
            nonlocal config
            if value is None:
                value = os.environ.get(name)
            if value is None:
                if config is None:
                    config = dotenv_values(Path.cwd() / ".env")
                value = config.get(name)
            return (value or "").strip()

        api_url = setting(api_url, "PROXY_GO_API")
        self._api_key = setting(api_key, "PROXY_GO_API_KEY")
        if not api_url or not self._api_key:
            raise ProxyGoConfigError("PROXY_GO_API and PROXY_GO_API_KEY are required for proxy-go")
        try:
            endpoint = httpx.URL(api_url)
        except httpx.InvalidURL as exc:
            raise ProxyGoConfigError("Invalid PROXY_GO_API URL") from exc
        if (endpoint.scheme not in ("http", "https") or not endpoint.host
                or endpoint.userinfo or endpoint.query or endpoint.fragment
                or endpoint.path.rstrip("/") not in ("", "/v1/fetch")):
            raise ProxyGoConfigError("PROXY_GO_API must be an HTTP(S) base URL or /v1/fetch endpoint")
        self.url = str(endpoint.copy_with(path="/v1/fetch"))
        self._client = httpx.Client(transport=transport, trust_env=False,
                                   follow_redirects=False, timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._client.close()

    @property
    def is_closed(self):
        return self._client.is_closed

    def get(self, url: str, *, country: str = "US") -> httpx.Response:
        return self.fetch(url, country=country)

    def head(self, url: str, *, country: str = "US") -> httpx.Response:
        return self.fetch(url, country=country, method="HEAD")

    def fetch(self, url: str, *, country: str = "US", method: str = "GET") -> httpx.Response:
        method = method.upper()
        country = country.upper()
        if method not in ("GET", "HEAD"):
            raise ValueError("proxy-go only supports GET and HEAD")
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError("proxy-go country must be a two-letter country code")
        target = httpx.Request(method, url)
        response = self._client.post(
            self.url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"url": str(target.url), "country": country, "method": method},
        )
        if response.status_code != 200:
            raise ProxyGoGatewayError(response, request=target)
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Expected a fetch response object")
            status = data["status_code"]
            headers = data["headers"]
            if (type(status) is not int or not 100 <= status <= 599
                    or not isinstance(headers, dict)
                    or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
                    or not isinstance(data["body_base64"], str)
                    or not isinstance(data["request_id"], str)
                    or not isinstance(data["country"], str)
                    or not isinstance(data["proxy_traffic"], (dict, type(None)))):
                raise ValueError("Invalid fetch response fields")
            body = base64.b64decode(data["body_base64"], validate=True)
            return httpx.Response(
                status, headers=headers, content=body, request=target,
                extensions={"proxy_go": {key: data[key] for key in
                                         ("request_id", "country", "proxy_traffic")}},
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise httpx.TransportError("Invalid proxy-go response", request=target) from exc
