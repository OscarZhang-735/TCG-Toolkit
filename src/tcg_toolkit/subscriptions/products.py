"""Read a subscription's current public offer from its exact merchant product page."""
from ..catalog.errors import ProductSourceError
from ..catalog.identity import favorite_source, normalize_favorite_url
from ..catalog.models import Game, SearchSource, StockStatus
from ..catalog.providers.storefront_details import fetch_storefront_detail
from ..catalog.providers.yuyutei_products import fetch_product_snapshot as yuyutei_snapshot


def fetch_product_snapshot(game, product_url, client_factory=None):
    game = Game(game)
    source = favorite_source(product_url)
    url = normalize_favorite_url(source, game, product_url)
    if source == SearchSource.YUYUTEI:
        return yuyutei_snapshot(game, url, client_factory)
    offer = fetch_storefront_detail(source, url, game=game, client_factory=client_factory)
    if (not offer['name'] or not offer['card_number'] or offer['price'] is None
            or offer['stock_status'] == StockStatus.UNKNOWN):
        raise ProductSourceError('product_unreadable')
    return offer
