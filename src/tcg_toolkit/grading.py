"""Authenticated grading certificate lookups through provider adapters."""
import os
import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup

import httpx
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from .auth.routes import current_user


router = APIRouter(prefix='/api/grading', dependencies=[Depends(current_user)])
PSA_API_URL = 'https://api.psacard.com/publicapi/cert'


def _psa_request(client, method, cert_number, *, images=False):
    try:
        response = client.get(f'{PSA_API_URL}/{method}/{cert_number}', timeout=20)
        if response.status_code in (401, 403):
            raise HTTPException(502, detail='psa_authentication_failed')
        if response.status_code == 429:
            raise HTTPException(502, detail='psa_rate_limited')
        response.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(504, detail='psa_timeout') from None
    except httpx.HTTPError:
        raise HTTPException(502, detail='psa_unavailable') from None
    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(502, detail='psa_unreadable') from None
    if not isinstance(payload, (dict, list)) or (not images and not isinstance(payload, dict)):
        raise HTTPException(502, detail='psa_unreadable')
    if isinstance(payload, dict):
        message = payload.get('ServerMessage', '')
        message = message.strip().rstrip('.').casefold() if isinstance(message, str) else ''
        if payload.get('IsValidRequest') is False:
            raise HTTPException(502, detail='psa_request_rejected')
        if message == 'no data found' and payload.get('IsValidRequest') is True:
            if images:
                return payload
            raise HTTPException(404, detail='grading_certificate_not_found')
        if message and message != 'request successful':
            raise HTTPException(502, detail='psa_request_rejected')
    return payload


def fetch_psa(cert_number):
    token = os.getenv('TCG_PSA_API_TOKEN', '').strip()
    if not token:
        raise HTTPException(503, detail='psa_not_configured')
    with httpx.Client(headers={'Authorization': f'bearer {token}', 'Accept': 'application/json'},
                      follow_redirects=False, trust_env=False) as client:
        certificate = _psa_request(client, 'GetByCertNumber', cert_number)
        records = [certificate.get(key) for key in ('PSACert', 'DNACert')]
        if not any(isinstance(record, dict) and record.get('CertNumber') == cert_number
                   for record in records):
            raise HTTPException(502, detail='psa_unreadable')
        images = _psa_request(client, 'GetImagesByCertNumber', cert_number, images=True)
    return {'provider': 'psa', 'cert_number': cert_number,
            'source_url': f'https://www.psacard.com/cert/{cert_number}/',
            'certificate': certificate, 'images': images}


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
