"""HTTP lookups for official trading card catalogs."""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import current_user
from .providers.official import (
    LYCEE_SEARCH, PM_SEARCH, SEV_DETAIL, SEV_EN_DETAIL, WS_EN_SEARCH, WS_SEARCH, WSR_DETAIL,
    WSR_SEARCH, parse_lycee, parse_pm, parse_sev, parse_sev_en, parse_ws, parse_ws_en, parse_wsr,
    wsrose_rarity,
)

router = APIRouter(prefix='/api/official', dependencies=[Depends(current_user)])


@router.get('/lycee/search')
def search(word: str = Query(min_length=1, max_length=100)):
    if not word.strip():
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(LYCEE_SEARCH, params={'word': word.strip()})
            response.raise_for_status()
        return {'items': parse_lycee(response.text, str(response.url))}
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail='official_timeout') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, detail='official_unavailable') from exc


@router.get('/ws/search')
def search_ws(word: str = Query(min_length=1, max_length=100)):
    if not word.strip():
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(WS_SEARCH, params={'keyword': word.strip()})
            response.raise_for_status()
        data = response.json()
        items = parse_ws(data)
        return {'items': items, 'total': int(data.get('total', len(items)))}
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail='official_timeout') from exc
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(502, detail='official_unavailable') from exc


@router.get('/wsr/search')
def search_wsr(word: str = Query(min_length=1, max_length=100)):
    if not word.strip():
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(WSR_SEARCH, params={'keyword': word.strip()})
            response.raise_for_status()
            items, total = parse_wsr(response.text)
            # The picker displays five candidates; only retrieve details it can show.
            items = items[:5]
            for item in items:
                detail = client.get(WSR_DETAIL, params={'cardno': item['card_number']})
                detail.raise_for_status()
                item['rarity'] = wsrose_rarity(detail.text, item['card_number'])
        return {'items': items, 'total': total}
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail='official_timeout') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, detail='official_unavailable') from exc


@router.get('/pm/search')
def search_pm(word: str = Query(min_length=1, max_length=100)):
    if not word.strip():
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(PM_SEARCH, params={'s_flg': 'on', 'keyword_card': word.strip()})
            response.raise_for_status()
        return {'items': parse_pm(response.text)}
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
def search_ws_en(word: str = Query(min_length=1, max_length=100)):
    if not word.strip():
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(WS_EN_SEARCH, params={'keyword': word.strip()})
            response.raise_for_status()
        return parse_ws_en(response.text)
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail='official_timeout') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, detail='official_unavailable') from exc
