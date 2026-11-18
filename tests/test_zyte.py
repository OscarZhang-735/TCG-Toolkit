import httpx
import pytest

from tcg_toolkit.external.zyte import ZyteAPIError, ZyteConfigError, extract_http_response


URL = "https://books.toscrape.com/"


@pytest.fixture
def respond(monkeypatch):
    monkeypatch.setenv("ZYTE_API_KEY", "local-test-key")

    def configure(status, payload):
        def post(client, endpoint, **kwargs):
            return httpx.Response(status, json=payload, request=httpx.Request("POST", endpoint))

        monkeypatch.setattr(httpx.Client, "post", post)

    return configure


@pytest.mark.parametrize("status,problem_type,reason", [
    (401, "/auth/key-not-found", "authentication_failed"),
    (403, "/auth/account-suspended", "account_forbidden"),
    (429, "/limits/over-user-limit", "rate_limited"),
    (451, "/download/domain-forbidden", "domain_forbidden"),
    (503, "/limits/over-global-limit", "rate_limited"),
    (500, "/server/timed-out", "timeout"),
    (500, "/server/internal", "unavailable"),
    (520, "/download/temporary-error", "temporary_download_error"),
    (521, "/download/internal-error", "permanent_download_error"),
])
def test_extract_api_failure(respond, status, problem_type, reason):
    respond(status, {"type": problem_type, "detail": "private upstream detail"})
    with pytest.raises(ZyteAPIError) as caught:
        extract_http_response(URL)
    assert caught.value.reason == reason
    assert caught.value.response.status_code == status
    assert "private upstream detail" not in str(caught.value)


def test_target_error_remains_a_target_response(respond):
    respond(200, {"url": URL, "statusCode": 403, "httpResponseBody": "ZGVuaWVk"})
    response = extract_http_response(URL)
    assert response.status_code == 403
    assert response.content == b"denied"


def test_malformed_extract_body(respond):
    respond(200, {"url": URL, "statusCode": 200, "httpResponseBody": "!invalid!"})
    with pytest.raises(httpx.DecodingError):
        extract_http_response(URL)


def test_missing_key(monkeypatch):
    monkeypatch.setenv("ZYTE_API_KEY", "")
    with pytest.raises(ZyteConfigError):
        extract_http_response(URL)


def test_response_headers_match_decoded_body(respond):
    respond(200, {"url": URL, "statusCode": 200, "httpResponseBody": "aGVsbG8=",
                  "httpResponseHeaders": [
                      {"name": "Content-Type", "value": "text/html; charset=utf-8"},
                      {"name": "Content-Encoding", "value": "gzip"},
                      {"name": "Content-Length", "value": "99"},
                  ]})
    response = extract_http_response(URL)
    assert response.text == "hello"
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == "5"
