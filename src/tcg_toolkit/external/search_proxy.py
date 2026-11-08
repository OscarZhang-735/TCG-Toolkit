"""HTML forwarding API used by default search clients (not an HTTP CONNECT proxy)."""
import os
import base64
import time
import uuid
from contextvars import ContextVar

direct_search = ContextVar("direct_search", default=False)

import httpx

DEFAULT_SEARCH_PROXY_URL = "http://62.234.212.90:8080/encrypted-proxy"


def proxy_url():
    return "" if direct_search.get() else os.getenv("TCG_SEARCH_PROXY_URL", DEFAULT_SEARCH_PROXY_URL)


class YuYuTeiProxyTransport(httpx.BaseTransport):
    """Relay on the wire while HTTPX keeps cookies scoped to the target host."""

    def __init__(self, *, transport=None):
        self.url = proxy_url()
        self.key_file = os.getenv('TCG_PROXY_KEY_FILE', '')
        self.transport = transport if transport is not None else httpx.HTTPTransport()

    def handle_request(self, request):
        if not self.url or request.url.host != 'yuyu-tei.jp':
            return self.transport.handle_request(request)
        return self.encrypted_request(request)

    def encrypted_request(self, request):
        from .proxy_wire import Wire
        from cryptography.exceptions import InvalidTag
        try:
            if not self.key_file:
                raise ValueError('TCG_PROXY_KEY_FILE is required for encrypted proxy')
            endpoint = httpx.URL(self.url)
            if endpoint.path not in ('', '/', '/encrypted-proxy') or endpoint.query or endpoint.fragment:
                raise ValueError('Expected proxy base URL or /encrypted-proxy endpoint')
            endpoint = endpoint.copy_with(path='/encrypted-proxy')
            wire = Wire(self.key_file)
            request_id = str(uuid.uuid4())
            packet = wire.seal('request', {
                'request_id': request_id, 'timestamp': time.time(),
                'method': request.method, 'url': str(request.url),
                'headers': [(k, v) for k, v in request.headers.multi_items()
                            if k.lower() not in ('host', 'accept-encoding')] + [('Accept-Encoding', 'identity')],
                'body': base64.b64encode(request.read()).decode(),
            })
            forwarded = httpx.Request('POST', endpoint, content=packet,
                headers={'Content-Type': 'application/octet-stream'}, extensions=request.extensions)
            response = self.transport.handle_request(forwarded)
            try:
                if response.status_code != 200:
                    raise ValueError('Encrypted proxy HTTP error')
                encrypted = bytearray()
                for chunk in response.iter_raw():
                    encrypted.extend(chunk)
                    if len(encrypted) > 16 * 1024 * 1024:
                        raise ValueError('Encrypted proxy response too large')
                result = wire.open('response', bytes(encrypted))
            finally:
                response.close()
            if result.get('request_id') != request_id:
                raise ValueError('Encrypted proxy response mismatch')
            return httpx.Response(result['status'], headers=result['headers'],
                                  content=base64.b64decode(result['body'], validate=True))
        except (OSError, ValueError, KeyError, TypeError, InvalidTag) as exc:
            raise httpx.TransportError('Encrypted proxy request failed', request=request) from exc

    def close(self):
        self.transport.close()


class SearchProxyClient:
    """Use encrypted forwarding for proxy requests; preserve HTTPX errors."""

    def __init__(self, *, transport=None):
        self.url = proxy_url()
        self.client = httpx.Client(transport=YuYuTeiProxyTransport(transport=transport),
                                   follow_redirects=True, trust_env=False)

    def get(self, url, *, timeout):
        return self.client.get(url, timeout=timeout)

    def close(self):
        self.client.close()

    @property
    def is_closed(self):
        return self.client.is_closed
