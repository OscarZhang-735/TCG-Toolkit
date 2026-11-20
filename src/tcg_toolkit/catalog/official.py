"""HTTP lookups for official trading card catalogs."""
from unicodedata import normalize
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import current_user
from .providers.official import (
    LYCEE_SEARCH, PM_SEARCH, SEV_DETAIL, SEV_EN_DETAIL, WS_EN_SEARCH, WS_SEARCH, WSR_DETAIL,
    WSR_SEARCH, parse_lycee, parse_pm, parse_sev, parse_sev_en, parse_ws, parse_ws_en, parse_wsr,
    wsrose_rarity,
)

router = APIRouter(prefix='/api/official', dependencies=[Depends(current_user)])


def _printings(items, word):
    def key(value):
        return normalize('NFKC', value).strip().casefold()
    number = key(word)
    return [item for item in items if key(item['card_number']) == number]


def _all_reported(items, total, next_page):
    """Read every result before deciding that a card number has a unique printing."""
    result = list(items)
    previous = items
    page = 1
    while len(result) < total:
        page += 1
        items = next_page(page)
        if not items or items == previous:
            raise ValueError('Incomplete official search results')
        result.extend(items)
        previous = items
    if len(result) != total:
        raise ValueError('Inconsistent official result count')
    return result


def _extra_page(client, search_url, word, view, page):
    response = client.get(urljoin(search_url, '/cardlist/cardsearch_ex'),
                          params={'keyword': word, 'view': view, 'page': page})
    response.raise_for_status()
    return response.text


@router.get('/lycee/search')
def search(word: str = Query(min_length=1, max_length=100), exact: bool = False):
    if not word.strip():
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            params = {'word': word.strip()}
            page = 1
            matches, previous = [], None
            while True:
                response = client.get(LYCEE_SEARCH, params=params)
                response.raise_for_status()
                items = parse_lycee(response.text, str(response.url), separate_rows=exact)
                if not exact:
                    return {'items': items}
                if items == previous:
                    raise ValueError('Repeated Lycee search page')
                matches.extend(_printings(items, word))
                page += 1
                if not BeautifulSoup(response.text, 'html.parser').select_one(f'#pagemenu button[name=page][value="{page}"]'):
                    return {'items': matches}
                previous = items
                params['page'] = page
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail='official_timeout') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, detail='official_unavailable') from exc


@router.get('/ws/search')
def search_ws(word: str = Query(min_length=1, max_length=100), exact: bool = False):
    if not word.strip():
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(WS_SEARCH, params={'keyword': word.strip()})
            response.raise_for_status()
            data = response.json()
            items = parse_ws(data)
            total = int(data.get('total', len(items)))
            if exact:
                def next_page(page):
                    result = client.get(WS_SEARCH, params={'keyword': word.strip(), 'page': page})
                    result.raise_for_status()
                    data = result.json()
                    if data.get('page') != page or data.get('total') != total:
                        raise ValueError('Inconsistent WS search page')
                    return parse_ws(data)
                return {'items': _printings(_all_reported(items, total, next_page), word)}
            return {'items': items, 'total': total}
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail='official_timeout') from exc
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(502, detail='official_unavailable') from exc


@router.get('/wsr/search')
def search_wsr(word: str = Query(min_length=1, max_length=100), exact: bool = False):
    if not word.strip():
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(WSR_SEARCH, params={'keyword': word.strip()})
            response.raise_for_status()
            items, total = parse_wsr(response.text)
            if exact:
                def next_page(page):
                    html = _extra_page(client, WSR_SEARCH, word.strip(), 'image', page)
                    return parse_wsr(html, fragment=True)[0]
                items = _printings(_all_reported(items, total, next_page), word)
            else:
                # Keep an exact printing in the five-card picker when prefix matches precede it.
                wanted = word.strip().casefold()
                items.sort(key=lambda item: item['card_number'].casefold() != wanted)
                items = items[:5]
            for item in items:
                detail = client.get(WSR_DETAIL, params={'cardno': item['card_number']})
                detail.raise_for_status()
                item['rarity'] = wsrose_rarity(detail.text, item['card_number'])
        return {'items': items, 'total': len(items) if exact else total}
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail='official_timeout') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, detail='official_unavailable') from exc


@router.get('/pm/search')
def search_pm(word: str = Query(min_length=1, max_length=100), exact: bool = False):
    if not word.strip():
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            params = {'s_flg': 'on', 'keyword_card': word.strip()}
            matches, previous = [], None
            page = 0  # Drupal's first results page is zero.
            while True:
                response = client.get(PM_SEARCH, params=params)
                response.raise_for_status()
                items = parse_pm(response.text)
                if not exact:
                    return {'items': items}
                if items == previous:
                    raise ValueError('Repeated PM search page')
                matches.extend(_printings(items, word))
                link = BeautifulSoup(response.text, 'html.parser').select_one('.pager-next a[href], a[rel~=next][href]')
                if link is None:
                    return {'items': matches}
                page += 1
                if parse_qs(urlsplit(link['href']).query).get('page') != [str(page)]:
                    raise ValueError('Unrecognized PM next page')
                previous = items
                params['page'] = page
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail='official_timeout') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, detail='official_unavailable') from exc


def _search_sev(word, *, english):
    number = word.strip()
    if not number:
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(SEV_EN_DETAIL if english else SEV_DETAIL, params={'cardno': number})
            response.raise_for_status()
        parse = parse_sev_en if english else parse_sev
        return {'items': parse(response.text, number)}
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail='official_timeout') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, detail='official_unavailable') from exc


@router.get('/sev_en/search')
def search_sev_en(word: str = Query(min_length=1, max_length=100)):
    return _search_sev(word, english=True)


@router.get('/sev/search')
def search_sev(word: str = Query(min_length=1, max_length=100)):
    return _search_sev(word, english=False)


@router.get('/ws_en/search')
def search_ws_en(word: str = Query(min_length=1, max_length=100), exact: bool = False):
    if not word.strip():
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(WS_EN_SEARCH, params={'keyword': word.strip()})
            response.raise_for_status()
            if not exact:
                return parse_ws_en(response.text, word.strip())
            result = parse_ws_en(response.text, limit=None)
            def next_page(page):
                html = _extra_page(client, WS_EN_SEARCH, word.strip(), 'text', page)
                return parse_ws_en(html, limit=None, fragment=True)['items']
            return {'items': _printings(_all_reported(result['items'], result['total'], next_page), word)}
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail='official_timeout') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, detail='official_unavailable') from exc
