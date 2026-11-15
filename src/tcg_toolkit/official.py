"""Official card metadata, independent of marketplace search."""
from urllib.parse import parse_qs, urljoin, urlsplit
import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Query
from .auth.dependencies import current_user

router = APIRouter(prefix='/api/official', dependencies=[Depends(current_user)])
LYCEE_SEARCH = 'https://lycee-tcg.com/card/'

def parse_lycee(html, base=LYCEE_SEARCH):
    soup = BeautifulSoup(html, 'html.parser')
    items = []
    seen = set()
    for link in soup.select('a[href*="card_detail.pl?cardno="]'):
        number = parse_qs(urlsplit(link['href']).query).get('cardno', [''])[0]
        row = link.find_parent('tr')
        cells = row.find_all('td', recursive=False) if row else []
        name = link.get_text(' ', strip=True)
        rarity = cells[-1].get_text(strip=True) if cells else ''
        image = row.select_one('img[src]') if row else None
        image_url = urljoin(base, image['src']) if image else None
        if image_url and urlsplit(image_url).scheme not in ('http', 'https'):
            image_url = None
        if not number or not name or not rarity:
            raise ValueError('Invalid official card row')
        if number not in seen:
            items.append(dict(game='Lycee', card_number=number, name=name, rarity=rarity, image_url=image_url))
            seen.add(number)
    if not items and not soup.select('input[name="word"]'):
        raise ValueError('Unrecognized official search page')
    return items

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


WS_SEARCH = 'https://ws-tcg.com/manage/CardListUser/searchJson'
WS_IMAGES = 'https://ws-tcg.com/wordpress/wp-content/images/cardlist/'


def parse_ws(data):
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('Unrecognized official WS response')
    items = []
    for row in data['items']:
        if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip()
                                           for k in ('card_number', 'card_name', 'rare')):
            raise ValueError('Invalid official WS card')
        picture = row.get('picture')
        image = urljoin(WS_IMAGES, picture) if isinstance(picture, str) and picture else None
        if image and not image.startswith(WS_IMAGES):
            image = None
        items.append(dict(game='WS', card_number=row['card_number'], name=row['card_name'],
                          rarity=row['rare'], image_url=image))
    return items


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


WSR_SEARCH = 'https://ws-rose.com/cardlist/searchresults/'
WSR_DETAIL = 'https://ws-rose.com/cardlist/'


def parse_wsr(html):
    soup = BeautifulSoup(html, 'html.parser')
    hits = soup.select_one('.search-Hits span')
    if hits is None:
        raise ValueError('Unrecognized WSR search page')
    total = int(hits.get_text(strip=True).replace(',', ''))
    items = []
    for link in soup.select('.cardlist-Result_Item .card-Item a[href]'):
        number = parse_qs(urlsplit(link['href']).query).get('cardno', [''])[0]
        image = link.select_one('img[src]')
        if not number or image is None or not image.get('alt'):
            raise ValueError('Invalid WSR card row')
        image_url = urljoin(WSR_SEARCH, image['src'])
        if not image_url.startswith('https://ws-rose.com/'):
            image_url = None
        items.append(dict(game='WSR', card_number=number, name=image['alt'], image_url=image_url))
    if total and not items:
        raise ValueError('Missing WSR results')
    return items, total


def wsrose_rarity(html, number):
    soup = BeautifulSoup(html, 'html.parser')
    fields = {}
    for term in soup.select('dt'):
        value = term.find_next_sibling('dd')
        if value:
            fields[term.get_text(strip=True)] = value.get_text(strip=True)
    if fields.get('カード番号') != number or not fields.get('レアリティ'):
        raise ValueError('Invalid WSR detail')
    return fields['レアリティ']


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


PM_SEARCH = 'http://www.p-memories.com/card_product_list_page'


def parse_pm(html):
    soup = BeautifulSoup(html, 'html.parser')
    if not soup.select_one('input[name="keyword_card"]'):
        raise ValueError('Unrecognized PM search page')
    items = []
    for row in soup.select('table tbody tr'):
        link = row.select_one('a.cardPreview[href]')
        if link is None:
            continue
        cells = row.find_all('td', recursive=False)
        if len(cells) < 6:
            raise ValueError('Invalid PM card row')
        rarity, number, name = (cells[i].get_text(' ', strip=True) for i in (0, 1, 5))
        if not number or not name:
            raise ValueError('Missing PM card metadata')
        image = urljoin(PM_SEARCH, link['src']) if link.get('src') else None
        if image and urlsplit(image).scheme not in ('http', 'https'):
            image = None
        items.append(dict(game='PM', card_number=number, name=name, rarity=rarity,
                          image_url=image, source_url=urljoin(PM_SEARCH, link['href'])))
    return items


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


SEV_DETAIL = 'https://shadowverse-evolve.com/cardlist/'
SEV_EN_DETAIL = 'https://en.shadowverse-evolve.com/cards/'
SEV_EN_RARITIES = {'Bronze': 'B', 'Bronze Rare': 'B', 'Silver': 'S', 'Silver Rare': 'S',
                   'Gold': 'G', 'Gold Rare': 'G', 'Legendary': 'L', 'Ultimate Rare': 'U',
                   'Super Legendary': 'SL', 'Special': 'SP', 'Super Special': 'SSP',
                   'Premium': 'P'}


def _parse_sev_detail(html, number, *, english):
    game = 'SEV EN' if english else 'SEV'
    base = SEV_EN_DETAIL if english else SEV_DETAIL
    soup = BeautifulSoup(html, 'html.parser')
    detail = soup.select_one('.cardlist-Detail_Box_Inner')
    if detail is None:
        if soup.select_one('form#searchForm'):
            return []
        raise ValueError('Unrecognized SEV detail page')
    printed = detail.select_one('.illustrator .heading')
    if printed is None:
        raise ValueError('Missing SEV card number')
    if printed.get_text(strip=True) != number:
        return []
    name = detail.select_one('h1.ttl')
    image = detail.select_one('.img img[src]')
    fields = {dt.get_text(strip=True): dt.find_next_sibling('dd').get_text(strip=True)
              for dt in detail.select('dt') if dt.find_next_sibling('dd')}
    rarity = fields.get('Rarity' if english else 'レアリティ')
    if name is None or not name.get_text(strip=True) or not rarity or image is None:
        raise ValueError('Missing SEV metadata')
    image_url = urljoin(base, image['src'])
    if urlsplit(image_url).scheme not in ('http', 'https'):
        image_url = None
    return [dict(game=game, card_number=number, name=name.get_text(' ', strip=True),
                 rarity=SEV_EN_RARITIES.get(rarity, rarity) if english else rarity, image_url=image_url)]


def parse_sev_en(html, number):
    return _parse_sev_detail(html, number, english=True)


def parse_sev(html, number):
    return _parse_sev_detail(html, number, english=False)


def _search_sev(word, *, english):
    number = word.strip()
    if not number:
        raise HTTPException(422, detail='empty_keyword')
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.get(SEV_EN_DETAIL if english else SEV_DETAIL, params={'cardno': number})
            response.raise_for_status()
        return {'items': _parse_sev_detail(response.text, number, english=english)}
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


WS_EN_SEARCH = 'https://en.ws-tcg.com/cardlist/searchresults/'


def parse_ws_en(html):
    soup = BeautifulSoup(html, 'html.parser')
    hits = soup.select_one('.c-search__results-item span')
    if hits is None:
        raise ValueError('Unrecognized WS EN results')
    total = int(hits.get_text(strip=True).replace(',', ''))
    items = []
    for link in soup.select('a[href*="?cardno="]')[:5]:
        number = parse_qs(urlsplit(link['href']).query).get('cardno', [''])[0]
        name = link.select_one('.ttl')
        image = link.select_one('.image img[src]')
        fields = {dt.get_text(strip=True): dt.find_next_sibling('dd').get_text(strip=True)
                  for dt in link.select('dt') if dt.find_next_sibling('dd')}
        rarity = fields.get('Rarity')
        if not number or name is None or not name.get_text(strip=True) or image is None or not rarity:
            raise ValueError('Invalid WS EN card')
        image_url = urljoin(WS_EN_SEARCH, image['src'])
        if urlsplit(image_url).scheme not in ('http', 'https'):
            image_url = None
        items.append(dict(game='WS EN', card_number=number, name=name.get_text(' ', strip=True),
                          rarity=rarity, image_url=image_url))
    if total and not items:
        raise ValueError('Missing WS EN cards')
    return {'items': items, 'total': total}


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
