"""Authenticated HTTP endpoints for grading providers and certificates."""
from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from ..auth.dependencies import current_user
from .lookup import PROVIDERS, lookup_certificate

router = APIRouter(prefix='/api/grading', dependencies=[Depends(current_user)])


@router.get('/providers')
def providers():
    return {'items': [{'id': item.id, 'name': item.name, 'timeout_seconds': item.timeout_seconds,
                       'scan_patterns': list(item.scan_patterns)} for item in PROVIDERS.values()]}


@router.get('/{provider}/certificates/{cert_number}')
async def certificate(provider: str, cert_number: str, user=Depends(current_user)):
    return await run_in_threadpool(lookup_certificate, provider, cert_number, user['id'])
