"""Fill missing merchant rarities from the official catalog for the same printing."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx

from ..models import Game, SearchSource
from .official import (LYCEE_SEARCH, PM_SEARCH, SEV_DETAIL, WS_SEARCH, WSR_SEARCH, WSR_DETAIL,
                       parse_lycee, parse_pm, parse_sev, parse_ws, parse_wsr, wsrose_rarity)


def ws_catalog_search(game, keyword, timeout):
    url = WSR_SEARCH if game == Game.WSR else WS_SEARCH
    response = httpx.get(url, params={'keyword': keyword}, follow_redirects=True, timeout=timeout)
    response.raise_for_status()
    if game == Game.WSR:
        return parse_wsr(response.text)
    data = response.json()
    cards = parse_ws(data)
    return cards, int(data.get('total', len(cards)))


def fill_rarities(page, game, timeout):
    empty = (None, '', '-', '－', '—')
    def identity(item):
        return item.card_number, item.name if game == Game.PM else ''

    keys = list(dict.fromkeys(identity(item) for item in page.items if item.rarity in empty))
    if not keys or game == Game.CHAOS:
        return page

    def get(url, **params):
        response = httpx.get(url, params=params, follow_redirects=True, timeout=min(timeout, 5))
        response.raise_for_status()
        return response

    def lookup(identity):
        number, name = identity
        # HS uses the shop prefix in place of the official series (e.g. WR-R01-001).
        prefix = {Game.WS: 'WS-', Game.WSR: 'WR-'}.get(game)
        short = bool(prefix and page.source == SearchSource.HOBBYSTATION and number.startswith(prefix))
        key = number[len(prefix):] if short else number
        try:
            if game == Game.WSR and '/' in key:
                return wsrose_rarity(get(WSR_DETAIL, cardno=key).text, key)
            if game in (Game.WS, Game.WSR):
                cards, total = ws_catalog_search(game, key, min(timeout, 5))
            elif game == Game.SEV:
                cards = parse_sev(get(SEV_DETAIL, cardno=key).text, key)
            elif game == Game.PM:
                cards = parse_pm(get(PM_SEARCH, s_flg='on', keyword_card=key).text)
            else:
                cards = parse_lycee(get(LYCEE_SEARCH, word=key).text)
            if short and '/' not in key:
                if total != len(cards):
                    return None
                matches = [c for c in cards if c['card_number'].partition('/')[2] == key]
            else:
                matches = [c for c in cards if c['card_number'] == key]
            if game == Game.PM:
                # PM reuses numbers such as 01-001 across titles.
                matches = [c for c in matches if c['name'] == name]
            if len(matches) == 1:
                card = matches[0]
                if game == Game.WSR:
                    return wsrose_rarity(get(WSR_DETAIL, cardno=card['card_number']).text, card['card_number'])
                return card['rarity']
        except (httpx.HTTPError, ValueError, TypeError):
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        found = dict(zip(keys, pool.map(lookup, keys)))
    return replace(page, items=[replace(item, rarity=found[identity(item)] or item.rarity)
                                if item.rarity in empty else item for item in page.items])
