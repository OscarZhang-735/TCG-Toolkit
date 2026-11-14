"""Authenticated grading certificate lookups through provider adapters."""
import logging
import re
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

import httpx
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from .auth.routes import current_user
from .external.proxy_go import ProxyGoClient, ProxyGoConfigError, ProxyGoGatewayError


router = APIRouter(prefix='/api/grading', dependencies=[Depends(current_user)])
PSA_URL = 'https://www.psacard.com/en-CA/cert/{cert_number}/psa'
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
            images.append({'url': url, 'alt': image['alt']})

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
    try:
        with ProxyGoClient() as client:
            response = client.get(PSA_URL.format(cert_number=cert_number), country='US')
    except ProxyGoConfigError:
        raise HTTPException(503, detail='psa_not_configured') from None
    except ProxyGoGatewayError as exc:
        logger.warning('PSA proxy-go gateway_status=%s request_id=%s proxy_total_bytes=%s proxy_attempts=%s',
                       exc.status_code, exc.request_id, exc.proxy_total_bytes, exc.proxy_attempts)
        if exc.status_code == 504:
            raise HTTPException(504, detail='psa_timeout') from None
        detail = ('psa_authentication_failed' if exc.status_code in (401, 403) else
                  'psa_rate_limited' if exc.status_code == 429 else 'psa_unavailable')
        raise HTTPException(502, detail=detail) from None
    except httpx.TimeoutException:
        raise HTTPException(504, detail='psa_timeout') from None
    except httpx.HTTPError:
        raise HTTPException(502, detail='psa_unavailable') from None

    metadata = response.extensions['proxy_go']
    traffic = metadata['proxy_traffic'] or {}
    logger.info('PSA proxy-go request_id=%s target_status=%s proxy_total_bytes=%s proxy_attempts=%s',
                metadata['request_id'], response.status_code, traffic.get('total_bytes'),
                len(traffic.get('attempts', [])))
    if response.status_code == 429:
        raise HTTPException(502, detail='psa_rate_limited')
    if response.status_code not in (200, 404):
        raise HTTPException(502, detail='psa_unavailable')
    if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'text/html':
        raise HTTPException(502, detail='psa_unreadable')
    result = parse_psa(response.text, cert_number)
    if response.status_code != 200:
        raise HTTPException(502, detail='psa_unavailable')
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
            'certificate': {'fields': fields, 'grade': grade,
                'population': {'total_grading': totals[0], 'total_submission': totals[1],
                               'grades': grades, 'remarks': remarks}},
            'images': []}


def fetch_ars(cert_number):
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
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


PROVIDERS = {'psa': fetch_psa, 'ars': fetch_ars}


@router.get('/{provider}/certificates/{cert_number}')
async def certificate(provider: str, cert_number: str):
    adapter = PROVIDERS.get(provider)
    if adapter is None:
        raise HTTPException(422, detail='unsupported_grading_provider')
    cert_number = cert_number.strip()
    if not re.fullmatch(r'[0-9]{1,32}', cert_number):
        raise HTTPException(422, detail='invalid_cert_number')
    return await run_in_threadpool(adapter, cert_number)
