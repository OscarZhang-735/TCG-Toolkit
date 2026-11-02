"""Verified subscription recipients; account login and recovery stay independent."""
import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update

from .auth.routes import current_user, write_guard, CodeRequest, CodeInput
from .database import email_lock
from .db_models import User, EmailChallenge

router = APIRouter(prefix='/api/subscriptions/notification-email')
PURPOSE = 'notification_email'


def notification_recipient(user):
    if user is None:
        return None
    if user.notification_email and user.notification_email_verified_at is not None:
        return user.notification_email
    return user.email if user.email_verified_at is not None else None


def settings(user):
    return {'email': notification_recipient(user),
            'custom_email': user.notification_email,
            'using_registration_email': user.notification_email is None}


def supersede(db, user_id):
    db.execute(update(EmailChallenge).where(
        EmailChallenge.user_id == user_id, EmailChallenge.purpose == PURPOSE,
        EmailChallenge.state.in_(('pending', 'active'))).values(state='superseded'))


@router.get('')
def get_settings(request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        return settings(db.get(User, user['id']))


@router.post('/code', dependencies=[Depends(write_guard)])
def send_code(body: CodeRequest, request: Request, user=Depends(current_user)):
    request.app.state.auth_limiter.check(('notification-email', user['id']), 5)
    return request.app.state.auth.verification.send(body.email, body.language, PURPOSE, user['id'])


@router.put('', dependencies=[Depends(write_guard)])
def confirm(body: CodeInput, request: Request, user=Depends(current_user)):
    auth = request.app.state.auth
    with auth.store.session() as db:
        account = db.get(User, user['id'], with_for_update=True)
        address = db.scalar(select(EmailChallenge.email).where(EmailChallenge.id == body.challenge_id,
                            EmailChallenge.user_id == user['id'], EmailChallenge.purpose == PURPOSE))
        if address:
            email_lock(db, address)
        challenge, error = auth.verification.check(db, body.challenge_id, body.code, PURPOSE, user_id=user['id'])
        if error is None:
            account.notification_email = challenge.email
            account.notification_email_verified_at = int(time.time())
            challenge.state = 'used'
            db.flush()
            supersede(db, user['id'])
            result = settings(account)
    if error:
        raise error
    return result


@router.delete('', dependencies=[Depends(write_guard)])
def reset(request: Request, user=Depends(current_user)):
    with request.app.state.auth.store.session() as db:
        account = db.get(User, user['id'], with_for_update=True)
        account.notification_email = account.notification_email_verified_at = None
        supersede(db, user['id'])
        return settings(account)
