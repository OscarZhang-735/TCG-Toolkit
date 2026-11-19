"""Merchant product URLs for favorites and YuYu-Tei offer and cart identities."""
import re
from urllib.parse import urlsplit

from .models import Game, SearchSource

GAME_PATHS = {7: 'ws', 8: 'chaos', 11: 'pm', 34: 'lo', 47: 'sev', 56: 'wsr'}
GAME_CODES = {game: 'lo' if game == Game.LYCEE else game.value for game in Game}
HOBBY_GAME_DIRS = {Game.LYCEE: 'lo', Game.WS: 'ws', Game.WSR: 'wr'}
CARDMAX_CATEGORIES = {Game.SEV: 'ct1740', Game.WS: 'ct1847'}

FAVORITE_HOSTS = {
    'yuyu-tei.jp': SearchSource.YUYUTEI,
    'www.hobbystation-single.jp': SearchSource.HOBBYSTATION,
    'www.cardmax.jp': SearchSource.CARDMAX,
}


def favorite_source(product_url: str) -> SearchSource:
    host = urlsplit(product_url).netloc.lower()
    if host not in FAVORITE_HOSTS:
        raise ValueError('Unsupported favorite product host')
    return FAVORITE_HOSTS[host]


def normalize_favorite_url(source: SearchSource, game: Game | str, value: str) -> str:
    if favorite_source(value) != source:
        raise ValueError('Favorite source does not match product URL')
    if source == SearchSource.YUYUTEI:
        return normalize_product_url(game, value)
    game = Game(game)
    if source == SearchSource.HOBBYSTATION and game in HOBBY_GAME_DIRS:
        path = f'/{HOBBY_GAME_DIRS[game]}/product/detail'
    elif source == SearchSource.CARDMAX and game in CARDMAX_CATEGORIES:
        path = '/shopdetail'
    else:
        raise ValueError(f"{source} does not support {game}.")
    url = urlsplit(value)
    if (url.scheme != 'https' or url.query or url.fragment
            or not re.fullmatch(rf'{path}/[0-9]{{1,32}}/?', url.path)):
        raise ValueError('Invalid product URL for this game')
    return 'https://' + url.netloc.lower() + url.path.rstrip('/')


def normalize_product_url(game, value):
    url = urlsplit(value)
    code = GAME_CODES[Game(game)]
    pattern = rf'/sell/{code}/card/([A-Za-z0-9_-][A-Za-z0-9_.-]{{0,63}})/([0-9]{{1,32}})/?'
    if (url.scheme != 'https' or url.netloc.lower() != 'yuyu-tei.jp'
            or url.query or url.fragment or not re.fullmatch(pattern, url.path)):
        raise ValueError('Invalid product URL for this game')
    return 'https://yuyu-tei.jp' + url.path.rstrip('/')
