"""Public clients for external services, loaded independently."""
from importlib import import_module

_CLIENTS = {
    "CurrencyClient": "..currency.client",
    "CurrencyConfigError": "..currency.client",
    "CurrencyRequestError": "..currency.client",
    "TargetCurrency": "..currency.client",
    "SearchProxyClient": ".search_proxy",
    "ProxyGoClient": ".proxy_go",
    "ProxyGoConfigError": ".proxy_go",
    "ProxyGoGatewayError": ".proxy_go",
}
__all__ = list(_CLIENTS)


def __getattr__(name):
    if name not in _CLIENTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(_CLIENTS[name], __name__), name)


def __dir__():
    return sorted(set(globals()) | set(__all__))
