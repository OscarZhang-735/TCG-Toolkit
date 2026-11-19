"""Select and verify the email address used for subscription notifications."""
import time

from sqlalchemy import select, update

from ..persistence.database import email_lock
from ..persistence.models import EmailChallenge, User

PURPOSE = 'notification_email'


def notification_recipient(user):
    if user is None:
        return None
    if user.notification_email and user.notification_email_verified_at is not None:
        return user.notification_email
    return user.email if user.email_verified_at is not None else None


def _settings(user):
    return {'email': notification_recipient(user),
            'custom_email': user.notification_email,
            'using_registration_email': user.notification_email is None}


def _supersede(db, user_id):
    db.execute(update(EmailChallenge).where(
        EmailChallenge.user_id == user_id, EmailChallenge.purpose == PURPOSE,
        EmailChallenge.state.in_(('pending', 'active'))).values(state='superseded'))


def get_settings(database, user_id):
    with database.session() as db:
        return _settings(db.get(User, user_id))


def confirm(database, verification, user_id, challenge_id, code):
    with database.session() as db:
        account = db.get(User, user_id, with_for_update=True)
        address = db.scalar(select(EmailChallenge.email).where(EmailChallenge.id == challenge_id,
                            EmailChallenge.user_id == user_id, EmailChallenge.purpose == PURPOSE))
        if address:
            email_lock(db, address)
        challenge, error = verification.check(db, challenge_id, code, PURPOSE, user_id=user_id)
        if error is None:
            account.notification_email = challenge.email
            account.notification_email_verified_at = int(time.time())
            challenge.state = 'used'
            db.flush()
            _supersede(db, user_id)
            return _settings(account)
    # A rejected verification still needs to commit its attempt counter.
    raise error


def reset(database, user_id):
    with database.session() as db:
        account = db.get(User, user_id, with_for_update=True)
        account.notification_email = account.notification_email_verified_at = None
        _supersede(db, user_id)
        return _settings(account)
