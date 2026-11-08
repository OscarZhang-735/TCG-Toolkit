"""Account operations and revocable persistent sessions."""
import hashlib
import secrets
import time
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from ..database import email_lock, is_unique_violation
from ..db_models import EmailChallenge, LoginSession, User
from .passwords import hasher, verify_password
from .email_verification import AuthError, EmailVerification, normalize_email

SESSION_SECONDS = 7 * 24 * 60 * 60


def public_user(user: User):
    return {**{key: getattr(user, key) for key in ("id", "username", "nickname", "created_at", "email", "avatar_url")},
            "email_verified": user.email_verified_at is not None}


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class DuplicateUsername(AuthError):
    def __init__(self):
        super().__init__("username_taken", 409)


class AuthService:
    def __init__(self, store, verification=None):
        self.store = store
        self.verification = verification if verification is not None else EmailVerification(store)

    def _session(self, db, user_id):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        db.execute(delete(LoginSession).where(LoginSession.user_id == user_id, LoginSession.expires_at <= now))
        db.add(LoginSession(token_hash=token_hash(token), user_id=user_id, expires_at=now + SESSION_SECONDS))
        return token

    def register(self, username, password, email, challenge_id, code):
        email = normalize_email(email)
        username = username.lower()
        user = User(id=str(uuid.uuid4()), username=username, created_at=int(time.time()),
                    email=email, email_verified_at=int(time.time()), password_hash=hasher.hash(password))
        with self.store.session() as db:
            email_lock(db, email)
            challenge, error = self.verification.check(db, challenge_id, code, "register", email=email)
            if error is None:
                if db.scalar(select(User.id).where(func.lower(User.username) == username)):
                    raise DuplicateUsername()
                if db.scalar(select(User.id).where(User.email == email)):
                    raise AuthError("email_taken", 409)
                try:
                    with db.begin_nested():
                        db.add(user)
                        db.flush()
                except IntegrityError as exc:
                    if is_unique_violation(exc, "users_username"):
                        raise DuplicateUsername() from None
                    if is_unique_violation(exc, "users_email_key"):
                        raise AuthError("email_taken", 409) from None
                    raise
                challenge.state = "used"
                token = self._session(db, user.id)
        if error:
            raise error
        return public_user(user), token

    def send_binding_code(self, user_id, email, password, language):
        with self.store.session() as db:
            user = db.get(User, user_id)
        if not verify_password(user.password_hash if user else None, password):
            raise AuthError("invalid_password", 400)
        return self.verification.send(email, language, "bind", user_id)

    def bind_email(self, user_id, challenge_id, code):
        with self.store.session() as db:
            user = db.get(User, user_id, with_for_update=True)
            email = db.scalar(select(EmailChallenge.email).where(EmailChallenge.id == challenge_id))
            if email:
                email_lock(db, email)
            challenge, error = self.verification.check(db, challenge_id, code, "bind", user_id=user_id)
            if error is None:
                if user.email is not None:
                    raise AuthError("email_already_bound", 409)
                if db.scalar(select(User.id).where(User.email == challenge.email)):
                    raise AuthError("email_taken", 409)
                user.email, user.email_verified_at = challenge.email, int(time.time())
                challenge.state = "used"
        if error:
            raise error
        return public_user(user)

    def login(self, username, password):
        username = username.strip()
        is_email = "@" in username
        if is_email:
            try:
                username = normalize_email(username)
            except ValueError:
                verify_password(None, password)
                return None
        with self.store.session() as db:
            # Keep password verification/session issuance serialized with changes.
            query = select(User).where(User.email == username, User.email_verified_at.is_not(None)) if is_email else (
                select(User).where(func.lower(User.username) == username.lower()))
            user = db.scalar(query.with_for_update())
            if not verify_password(user.password_hash if user else None, password):
                return None
            if hasher.check_needs_rehash(user.password_hash):
                user.password_hash = hasher.hash(password)
            return public_user(user), self._session(db, user.id)

    def send_password_code(self, user_id, language):
        with self.store.session() as db:
            user = db.get(User, user_id)
        if not user or not user.email or user.email_verified_at is None:
            raise AuthError("verified_email_required")
        return self.verification.send(user.email, language, "password_change", user_id)

    def _replace_password(self, db, user, encoded):
        user.password_hash = encoded
        db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
        # Pending SMTP deliveries must not reactivate after a password change.
        db.execute(update(EmailChallenge).where(
            EmailChallenge.user_id == user.id, EmailChallenge.state.in_(("pending", "active"))
        ).values(state="superseded"))

    def change_password(self, user_id, token, new_password, method, current_password=None,
                        challenge_id=None, code=None):
        encoded = hasher.hash(new_password)
        with self.store.session() as db:
            user = db.get(User, user_id, with_for_update=True)
            session = db.scalar(select(LoginSession).where(
                LoginSession.token_hash == token_hash(token), LoginSession.user_id == user_id,
                LoginSession.expires_at > int(time.time())))
            if not session:
                raise AuthError("authentication_required", 401)
            error = None
            if method == "password":
                if not verify_password(user.password_hash, current_password):
                    error = AuthError("invalid_password")
            else:
                if not user.email or user.email_verified_at is None:
                    raise AuthError("verified_email_required")
                challenge, error = self.verification.check(db, challenge_id, code, "password_change",
                                                         email=user.email, user_id=user_id)
            if error is None:
                if method == "email":
                    challenge.state = "used"
                self._replace_password(db, user, encoded)
        if error:
            raise error

    def reset_password(self, email, challenge_id, code, new_password):
        encoded = hasher.hash(new_password)
        with self.store.session() as db:
            user = db.scalar(select(User).where(User.email == email, User.email_verified_at.is_not(None)).with_for_update())
            if not user:
                raise AuthError("invalid_code")
            challenge, error = self.verification.check(db, challenge_id, code, "password_reset",
                                                      email=email, user_id=user.id)
            if error is None:
                challenge.state = "used"
                self._replace_password(db, user, encoded)
        if error:
            raise error

    def current_user(self, token):
        if not token:
            return None
        with self.store.session() as db:
            user = db.scalar(select(User).join(LoginSession, User.id == LoginSession.user_id).where(
                LoginSession.token_hash == token_hash(token), LoginSession.expires_at > time.time()))
            return public_user(user) if user else None

    def logout(self, token):
        with self.store.session() as db:
            db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(token)))
