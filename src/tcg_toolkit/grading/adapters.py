"""Provider-specific retrieval and normalization of grading certificates."""
import logging
import re
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException

from ..external.zyte import extract_http_response, ZyteConfigError, ZyteAPIError
from .concurrency import cancellable_client


PSA_URL = 'https://www.psacard.com/en-CA/cert/{cert_number}/psa'
CGC_URL = 'https://www.cgccards.com/certlookup/{cert_number}/'
PSA_SIDES = {'Cert image 1': 'front', 'Cert image 2': 'back'}
CGC_SIDES = {'obverse': 'front', 'reverse': 'back'}
logger = logging.getLogger(__name__)


def parse_psa(html, cert_number):
    soup = BeautifulSoup(html, 'html.parser')

    def unreadable():
        raise HTTPException(502, detail='psa_unreadable')

    # PSA's streamed Next.js page can carry an explicit 404 inside HTTP 200.
    if soup.select_one('html#__next_error__') is not None and any(
            'NEXT_HTTP_ERROR_FALLBACK;404' in script.get_text() for script in soup.select('script')):
        raise HTTPException(404, detail='grading_certificate_not_found')
    main = soup.select_one('main')
    if main is None:
        unreadable()
    fields = {}
    for label in main.select('dl dt'):
        value = label.find_next_sibling('dd')
        key = node_text(label)
        if not key or key in fields or value is None:
            unreadable()
        fields[key] = node_text(value)
    title = node_text(main.select_one('h1'))
    if fields.get('Cert Number') != cert_number or not fields.get('Item Grade') or not title:
        unreadable()

    images = []
    for image in main.find_all('img', alt=re.compile(r'^Cert image [0-9]+$')):
        source = image.get('src', '')
        url = urljoin(PSA_URL.format(cert_number=cert_number), source)
        parsed = urlsplit(url)
        if not source or parsed.scheme not in ('http', 'https') or not parsed.netloc:
            unreadable()
        if not any(item['url'] == url for item in images):
            # PSA lists the slab front first, then the back; src is already a small image.
            images.append({'url': url, 'alt': image['alt'], 'side': PSA_SIDES.get(image['alt']),
                           'thumbnail_url': None})

    # Population panels may be streamed outside <main>; absent counts stay null.
    population = {'at_grade': None, 'higher': None}
    labels = {'PSA Population': 'at_grade', 'PSA Pop Higher': 'higher'}
    for group in soup.select('[role="group"][aria-labelledby]'):
        label = group.find(id=group['aria-labelledby'])
        key = labels.get(node_text(label))
        if key:
            value = node_text(label.find_next_sibling())
            if re.fullmatch(r'[0-9]+(?:,[0-9]{3})*', value):
                population[key] = int(value.replace(',', ''))
    return {'provider': 'psa', 'cert_number': cert_number,
            'source_url': PSA_URL.format(cert_number=cert_number),
            'certificate': {'title': title, 'grade': fields['Item Grade'],
                            'fields': fields, 'population': population},
            'images': images}


def fetch_psa(cert_number):
    return _fetch_proxied_certificate('psa', cert_number,
                                     PSA_URL.format(cert_number=cert_number), parse_psa)


def parse_cgc(html, cert_number):
    soup = BeautifulSoup(html, 'html.parser')

    def unreadable():
        raise HTTPException(502, detail='cgc_unreadable')

    intro = soup.select_one('.certlookup-intro')
    if intro is None:
        unreadable()
    # The search form includes inactive not-found and rate-limit templates even
    # on successful pages. Only the server-rendered certificate fields count.
    fields = {}
    for label in intro.select('dl dt'):
        key = node_text(label)
        value = label.find_next_sibling('dd')
        if not key or key in fields or value is None:
            unreadable()
        fields[key] = node_text(value)
    if (fields.get('Cert #') != cert_number or not fields.get('Card Name')
            or not fields.get('Grade')):
        unreadable()

    source_url = CGC_URL.format(cert_number=cert_number)
    images = []
    for image in soup.select('.certlookup-images img'):
        link = image.find_parent('a')
        source = (link.get('href') if link else None) or image.get('src')
        if not source:
            unreadable()
        try:
            url = urljoin(source_url, source)
            parsed = urlsplit(url)
        except ValueError:
            unreadable()
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            unreadable()
        if not any(item['url'] == url for item in images):
            alt = image.get('title') or image.get('alt', '')
            images.append({'url': url, 'alt': alt, 'side': CGC_SIDES.get(alt.strip().lower()),
                           'thumbnail_url': _thumbnail(source_url, image.get('src'), url)})

    population = {'at_grade': None, 'higher': None}
    labels = {f"In {fields['Grade']} Grade": 'at_grade', 'In Higher Grades': 'higher'}
    for value in soup.select('.certlookup-stats-item-value'):
        label, _, count = node_text(value).partition(':')
        key = labels.get(label)
        count = count.strip()
        # Unresolved Angular expressions and "Coming Soon!" are not counts.
        if key and re.fullmatch(r'[0-9]+(?:,[0-9]{3})*', count):
            population[key] = int(count.replace(',', ''))
    return {'provider': 'cgc', 'cert_number': cert_number, 'source_url': source_url,
            'certificate': {'title': fields['Card Name'], 'grade': fields['Grade'],
                            'fields': fields, 'population': population},
            'images': images}


def _thumbnail(base, source, original):
    """The page's own small image (CGC ``TN_`` files); optional, so bad values are dropped."""
    if not source:
        return None
    try:
        url = urljoin(base, source)
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or url == original:
        return None
    return url


def fetch_cgc(cert_number):
    return _fetch_proxied_certificate('cgc', cert_number,
                                     CGC_URL.format(cert_number=cert_number), parse_cgc)


def _fetch_proxied_certificate(provider, cert_number, url, parse):
    try:
        response = extract_http_response(url, on_client=cancellable_client)
    except ZyteConfigError:
        raise HTTPException(503, detail=f'{provider}_not_configured') from None
    except ZyteAPIError as exc:
        logger.warning('%s zyte api_status=%s reason=%s', provider.upper(),
                       exc.response.status_code, exc.reason)
        if exc.reason == 'timeout':
            raise HTTPException(504, detail=f'{provider}_timeout') from None
        detail = ('authentication_failed' if exc.reason in ('authentication_failed', 'account_forbidden') else
                  'rate_limited' if exc.reason == 'rate_limited' else 'unavailable')
        raise HTTPException(502, detail=f'{provider}_{detail}') from None
    except httpx.TimeoutException:
        raise HTTPException(504, detail=f'{provider}_timeout') from None
    except httpx.HTTPError:
        raise HTTPException(502, detail=f'{provider}_unavailable') from None

    logger.info('%s zyte target_status=%s', provider.upper(), response.status_code)
    if response.status_code == 429:
        raise HTTPException(502, detail=f'{provider}_rate_limited')
    if response.status_code not in (200, 404):
        raise HTTPException(502, detail=f'{provider}_unavailable')
    if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'text/html':
        raise HTTPException(502, detail=f'{provider}_unreadable')
    result = parse(response.text, cert_number)
    if response.status_code != 200:
        raise HTTPException(502, detail=f'{provider}_unavailable')
    return result


ARS_URL = 'https://ars-grading.com/grading/searchNumber'
NOT_FOUND = '入力された鑑定番号のデータは存在しません。'


def node_text(node):
    return ' '.join(node.get_text(' ', strip=True).split()) if node else ''


def parse_ars(html, cert_number):
    main = BeautifulSoup(html, 'html.parser').select_one('main')
    def unreadable():
        raise HTTPException(502, detail='ars_unreadable')

    if main is None:
        unreadable()
    echo = main.select_one('input#word')
    if echo is None or echo.get('value') != cert_number:
        unreadable()
    if any(node_text(n) == NOT_FOUND for n in main.select('.error-mes')):
        raise HTTPException(404, detail='grading_certificate_not_found')
    fields = {}
    for row in main.select('.card-info table tr'):
        label, value = row.select_one('th'), row.select_one('td')
        if label is None or value is None or not node_text(label):
            unreadable()
        fields[node_text(label)] = node_text(value)
    highlights = main.select('.highlight-grade')
    if (not fields or not fields.get('カード名称') or not fields.get('Card Name')
            or len(highlights) != 1 or not highlights[0].get('data-grade')):
        unreadable()
    grade = highlights[0]['data-grade']
    grades = {}
    for entry in main.select('.grade-table .grade-entry'):
        spans = entry.select('span')
        key = entry.get('data-grade')
        if not key or key in grades or len(spans) != 2 or not node_text(spans[1]):
            unreadable()
        grades[key] = node_text(spans[1])
    if grade not in grades:
        unreadable()
    totals = []
    for selector, label in [('.totals .main', 'TOTAL GRADING'), ('.totals .sub', 'TOTAL SUBMISSION')]:
        match = re.search(label + r'\s*([0-9][0-9,]*)', node_text(main.select_one(selector)))
        if not match:
            unreadable()
        totals.append(int(match[1].replace(',', '')))
    remarks = node_text(main.select_one('.grade-table .remarks'))
    if not remarks:
        unreadable()
    return {'provider': 'ars', 'cert_number': cert_number,
            'source_url': ARS_URL + '?' + urlencode({'id': cert_number}),
            'certificate': {'title': fields['Card Name'], 'fields': fields, 'grade': grade,
                'population': {'total_grading': totals[0], 'total_submission': totals[1],
                               'grades': grades, 'remarks': remarks}},
            'images': []}


def fetch_ars(cert_number):
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            cancellable_client(client)
            response = client.get(ARS_URL, params={'id': cert_number})
        if response.status_code == 429:
            raise HTTPException(502, detail='ars_rate_limited')
        response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(504, detail='ars_timeout') from None
    except httpx.HTTPError:
        raise HTTPException(502, detail='ars_unavailable') from None
    if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'text/html':
        raise HTTPException(502, detail='ars_unreadable')
    return parse_ars(response.text, cert_number)


PCGCN_API = 'https://card.pcgcard.cn/api/card/card/search_card_result/'
PCGCN_URL = 'https://card.pcgcard.cn/h5/index.html#/pages/f/r?type=1&query='


def parse_pcgcn(payload, cert_number):
    def unreadable():
        raise HTTPException(502, detail='pcgcn_unreadable')

    if not isinstance(payload, dict):
        unreadable()
    if payload.get('code') == 400 and payload.get('data') is None and payload.get('msg') == '卡片不存在':
        raise HTTPException(404, detail='grading_certificate_not_found')
    data = payload.get('data')
    if payload.get('code') != 2000 or not isinstance(data, dict):
        unreadable()
    if (data.get('proof_number') != cert_number or not isinstance(data.get('name'), str)
            or not data['name'].strip() or not isinstance(data.get('score'), str) or not data['score'].strip()):
        unreadable()
    # Only certificate fields: never persist logistics or account metadata.
    labels = {'proof_number': 'Cert Number', 'name': 'Card Name', 'version': 'Set',
              'card_number': 'Card Number', 'year': 'Year', 'language': 'Category',
              'region_language': 'Language', 'c2j': 'Original Name', 'score': 'Grade',
              'box': 'Centering', 'corner': 'Corners', 'edge': 'Edges', 'face': 'Surface',
              'level_number': 'Grade Label', 'loss': 'Grading Notes'}
    fields = {label: str(data[key]) for key, label in labels.items()
              if isinstance(data.get(key), (str, int, float)) and data[key] != ''}
    population = {}
    distribution = data.get('distribution')
    if distribution is not None:
        if not isinstance(distribution, list):
            unreadable()
        grades = {}
        for entry in distribution:
            if (not isinstance(entry, dict) or not isinstance(entry.get('score'), str)
                    or type(entry.get('count')) is not int or entry['count'] < 0):
                unreadable()
            grades[entry['score']] = entry['count']
        population['grades'] = grades
    if type(data.get('total_cards')) is int and data['total_cards'] >= 0:
        population['total_grading'] = data['total_cards']
    images = []
    pictures = data.get('carousel_pic') or ''
    if not isinstance(pictures, str):
        unreadable()
    for source in pictures.split(','):
        url = source.strip()
        if not url:
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            unreadable()
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            unreadable()
        if not any(image['url'] == url for image in images):
            images.append({'url': url, 'alt': f'Certificate image {len(images) + 1}',
                           'side': None, 'thumbnail_url': None})
    return {'provider': 'pcgcn', 'cert_number': cert_number, 'source_url': PCGCN_URL + cert_number,
            'certificate': {'title': data['name'], 'grade': data['score'], 'fields': fields,
                            'population': population}, 'images': images}


def fetch_pcgcn(cert_number):
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            cancellable_client(client)
            response = client.get(PCGCN_API, params={'pageNo': 1, 'pageSize': 1, 'proof_number': cert_number})
        if response.status_code == 429:
            raise HTTPException(502, detail='pcgcn_rate_limited')
        response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(504, detail='pcgcn_timeout') from None
    except httpx.HTTPError:
        raise HTTPException(502, detail='pcgcn_unavailable') from None
    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(502, detail='pcgcn_unreadable') from None
    return parse_pcgcn(payload, cert_number)
