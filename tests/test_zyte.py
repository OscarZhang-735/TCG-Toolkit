import httpx
import pytest

from tcg_toolkit.external.zyte import ZyteAPIError, extract_http_response


URL = "https://books.toscrape.com/"


@pytest.fixture
def respond(monkeypatch):
    monkeypatch.setenv("ZYTE_API_KEY", "local-test-key")

    def configure(status, payload):
        def post(client, endpoint, **kwargs):
            return httpx.Response(status, json=payload, request=httpx.Request("POST", endpoint))

        monkeypatch.setattr(httpx.Client, "post", post)

    return configure


def test_rate_limit(respond):
    respond(429, {"type": "/limits/over-user-limit", "detail": "private upstream detail"})
    with pytest.raises(ZyteAPIError) as caught:
        extract_http_response(URL)
    assert caught.value.reason == "rate_limited"
    assert caught.value.response.status_code == 429
    assert "private upstream detail" not in str(caught.value)


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
