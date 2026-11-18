"""Retrieve HTTP response bodies from the Zyte Extract API."""
import base64
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values


class ZyteConfigError(ValueError):
    """The server has no extract API key."""


class ZyteAPIError(httpx.HTTPStatusError):
    """An Extract API error, distinct from the target site's HTTP status."""

    def __init__(self, response: httpx.Response):
        status = response.status_code
        try:
            problem = response.json()
        except ValueError:
            problem = None
        problem_type = problem.get("type") if isinstance(problem, dict) else None
        if status == 500 and problem_type == "/server/timed-out":
            self.reason = "timeout"
        else:
            self.reason = {
                400: "invalid_request", 401: "authentication_failed", 403: "account_forbidden",
                421: "domain_unreachable", 422: "invalid_request", 429: "rate_limited",
                451: "domain_forbidden", 503: "rate_limited", 504: "timeout",
                520: "temporary_download_error", 521: "permanent_download_error",
            }.get(status, "unavailable")
        super().__init__(f"Zyte API HTTP {status} ({self.reason})",
                         request=response.request, response=response)


def extract_http_response(url: str, *, on_client=None) -> httpx.Response:
    """Return the target status and decoded body. API failures raise ZyteAPIError."""
    key = os.getenv("ZYTE_API_KEY")
    if key is None:
        key = dotenv_values(Path.cwd() / ".env").get("ZYTE_API_KEY")
    if not key or not key.strip():
        raise ZyteConfigError("ZYTE_API_KEY is required")

    with httpx.Client(trust_env=False, timeout=httpx.Timeout(220, connect=5)) as client:
        if on_client is not None:
            on_client(client)
        response = client.post(
            "https://api.zyte.com/v1/extract",
            auth=(key.strip(), ""),
            json={"url": url, "httpResponseBody": True, "httpResponseHeaders": True, "followRedirect": True},
        )
    if response.status_code != 200:
        raise ZyteAPIError(response)
    try:
        result = response.json()
        status = result["statusCode"]
        if type(status) is not int or not 100 <= status <= 599:
            raise ValueError("Invalid target HTTP status")
        body = base64.b64decode(result["httpResponseBody"], validate=True)
        request = httpx.Request("GET", result["url"])
        # Zyte already decompresses the body; original wire headers no longer apply.
        headers = {item['name']: item['value'] for item in result.get('httpResponseHeaders', [])
                   if item['name'].lower() not in ('content-encoding', 'content-length', 'transfer-encoding')}
    except (KeyError, TypeError, ValueError, httpx.InvalidURL) as exc:
        raise httpx.DecodingError("Invalid Zyte extract response", request=response.request) from exc
    return httpx.Response(status, content=body, headers=headers, request=request)
