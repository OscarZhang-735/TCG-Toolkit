"""Verified subscription recipients; account login and recovery stay independent."""
from fastapi import APIRouter, Depends, Request

from ..auth.dependencies import current_user, write_guard
from ..auth.schemas import CodeRequest, CodeInput
from . import recipients

router = APIRouter(prefix='/api/subscriptions/notification-email')


@router.get('')
def get_settings(request: Request, user=Depends(current_user)):
    return recipients.get_settings(request.app.state.database, user['id'])


@router.post('/code', dependencies=[Depends(write_guard)])
def send_code(body: CodeRequest, request: Request, user=Depends(current_user)):
    request.app.state.auth_limiter.check(('notification-email', user['id']), 5)
    return request.app.state.auth.verification.send(body.email, body.language, recipients.PURPOSE, user['id'])


@router.put('', dependencies=[Depends(write_guard)])
def confirm(body: CodeInput, request: Request, user=Depends(current_user)):
    return recipients.confirm(request.app.state.database, request.app.state.auth.verification,
                              user['id'], body.challenge_id, body.code)


@router.delete('', dependencies=[Depends(write_guard)])
def reset(request: Request, user=Depends(current_user)):
    return recipients.reset(request.app.state.database, user['id'])
