"""YuYu-Tei product identities shared by search, offers and cart inputs."""
import re
from urllib.parse import urlsplit

from .models import Game

GAME_PATHS = {7: 'ws', 8: 'chaos', 11: 'pm', 34: 'lo', 47: 'sev', 56: 'wsr'}
GAME_CODES = {game: 'lo' if game == Game.LYCEE else game.value for game in Game}


def normalize_product_url(game, value):
    url = urlsplit(value)
    code = GAME_CODES[Game(game)]
    pattern = rf'/sell/{code}/card/([A-Za-z0-9_-][A-Za-z0-9_.-]{{0,63}})/([0-9]{{1,32}})/?'
    if (url.scheme != 'https' or url.netloc.lower() != 'yuyu-tei.jp'
            or url.query or url.fragment or not re.fullmatch(pattern, url.path)):
        raise ValueError('Invalid product URL for this game')
    return 'https://yuyu-tei.jp' + url.path.rstrip('/')
