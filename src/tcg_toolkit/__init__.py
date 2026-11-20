"""Public search API. Load a provider only when a caller requests it."""
from importlib import import_module

_EXPORTS = {
    "create_searcher": ".catalog.search",
    "BaseSearcher": ".catalog.providers.yuyutei",
    "SEVSearcher": ".catalog.providers.yuyutei",
    "WSSearcher": ".catalog.providers.yuyutei",
    "PMSearcher": ".catalog.providers.yuyutei",
    "WSRSearcher": ".catalog.providers.yuyutei",
    "LyceeSearcher": ".catalog.providers.yuyutei",
    "ChaosTCGSearcher": ".catalog.providers.yuyutei",
    "HobbyStationLyceeSearcher": ".catalog.providers.hobbystation",
    "HobbyStationWSSearcher": ".catalog.providers.hobbystation",
    "HobbyStationWSRSearcher": ".catalog.providers.hobbystation",
    "CardMaxSEVSearcher": ".catalog.providers.cardmax",
    "CardMaxWSSearcher": ".catalog.providers.cardmax",
    "Game": ".catalog.models",
    "SearchSource": ".catalog.models",
    "StockStatus": ".catalog.models",
    "SearchItem": ".catalog.models",
    "SearchPage": ".catalog.models",
    "SearchError": ".catalog.errors",
    "SearchRequestError": ".catalog.errors",
    "SearchParseError": ".catalog.errors",
    "SearchPaginationError": ".catalog.errors",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(_EXPORTS[name], __name__), name)


def __dir__():
    return sorted(set(globals()) | set(__all__))
