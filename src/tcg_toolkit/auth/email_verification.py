"""Persistent, purpose-bound email challenges and synchronous SMTP delivery."""
from email.message import EmailMessage
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import ssl
import time

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from ..database import email_lock
from ..db_models import EmailChallenge, User
from email_validator import validate_email
from fastapi import HTTPException


def normalize_email(value):
    return validate_email(value.strip(), check_deliverability=False).normalized.lower()


class AuthError(HTTPException):
    def __init__(self, detail, status_code=400, retry_after=None):
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        super().__init__(status_code, detail=detail, headers=headers)


class SMTPMailer:
    def __init__(self):
        self.host = os.getenv("TCG_SMTP_HOST", "")
        self.username = os.getenv("TCG_SMTP_USERNAME", "")
        self.password = os.getenv("TCG_SMTP_PASSWORD", "")
        self.sender = os.getenv("TCG_SMTP_FROM", "")
        self.mode = os.getenv("TCG_SMTP_TLS", "starttls")
        try:
            self.port = int(os.getenv("TCG_SMTP_PORT") or ("465" if self.mode == "ssl" else "587"))
        except ValueError:
            self.port = 0
        self.configured = bool(self.host and self.sender and 0 < self.port < 65536
                               and self.mode in ("ssl", "starttls")
                               and bool(self.username) == bool(self.password)
                               and "\n" not in self.sender and "\r" not in self.sender)

    def send_code(self, email, code, language, purpose):
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = email
        if language == "zh-CN":
            action = {"register": "注册账户", "bind": "绑定邮箱",
                      "password_change": "修改密码", "password_reset": "重设密码"}[purpose]
            message["Subject"] = "TCG Toolkit 邮箱验证码"
            message.set_content(f"你正在{action}。\n\n验证码：{code}\n\n验证码在 10 分钟内有效，请勿向他人透露。\n如果不是你本人操作，请忽略此邮件。")
        else:
            action = {"register": "create an account", "bind": "link your email",
                      "password_change": "change your password", "password_reset": "reset your password"}[purpose]
            message["Subject"] = "TCG Toolkit email verification code"
            message.set_content(f"You requested to {action}.\n\nYour code: {code}\n\nThis code expires in 10 minutes. Do not share it.\nIf you did not request this, ignore this email.")
        context = ssl.create_default_context()
        if self.mode == "ssl":
            connection = smtplib.SMTP_SSL(self.host, self.port, timeout=10, context=context)
        else:
            connection = smtplib.SMTP(self.host, self.port, timeout=10)
        with connection as smtp:
            if self.mode == "starttls":
                smtp.starttls(context=context)
            if self.username:
                smtp.login(self.username, self.password)
            if smtp.send_message(message):
                raise smtplib.SMTPException("Recipient refused")


class EmailVerification:
    def __init__(self, store, mailer=None, secret=None):
        self.store = store
        self.mailer = mailer if mailer is not None else SMTPMailer()
        self.secret = (secret if secret is not None else os.getenv("TCG_EMAIL_CODE_SECRET", "")).encode()

    def require_configured(self):
        if len(self.secret) < 32 or not self.mailer.configured:
            raise AuthError("email_unavailable", 503)

    def digest(self, challenge_id, email, purpose, user_id, code):
        payload = json.dumps([challenge_id, email, purpose, user_id, code], separators=(",", ":"))
        return hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()

    def send(self, email, language="en", purpose="register", user_id=None):
        self.require_configured()
        now = int(time.time())
        challenge_id, code = secrets.token_urlsafe(32), f"{secrets.randbelow(1000000):06d}"
        digest = self.digest(challenge_id, email, purpose, user_id, code)
        with self.store.session() as db:
            # All auth flows acquire account, mailbox, then challenge locks.
            if user_id:
                db.get(User, user_id, with_for_update=True)
            elif purpose == "password_reset":
                db.scalar(select(User).where(User.email == email).with_for_update())
            email_lock(db, email)
            if purpose in ("password_change", "password_reset"):
                user = db.scalar(select(User).where(User.email == email, User.email_verified_at.is_not(None)))
                if not user or (user_id is not None and user.id != user_id):
                    if purpose == "password_change":
                        raise AuthError("verified_email_required")
                    return {"challenge_id": challenge_id, "expires_in": 600, "retry_after": 60}
                user_id = user.id
                digest = self.digest(challenge_id, email, purpose, user_id, code)
            elif user_id:
                user = db.get(User, user_id)
                if not user or user.email is not None:
                    raise AuthError("email_already_bound", 409)
            if purpose in ("register", "bind") and db.scalar(select(User.id).where(User.email == email)):
                raise AuthError("email_taken", 409)
            recent = db.scalars(select(EmailChallenge.created_at).where(
                EmailChallenge.email == email, EmailChallenge.created_at > now - 3600
            ).order_by(EmailChallenge.created_at)).all()
            retry = max(0, recent[-1] + 60 - now) if recent else 0
            if len(recent) >= 5:
                retry = max(retry, recent[-5] + 3600 - now)
            if retry:
                raise AuthError("rate_limited", 429, retry)
            db.add(EmailChallenge(id=challenge_id, email=email, purpose=purpose, user_id=user_id,
                                 code_hash=digest, created_at=now, expires_at=now + 600, state="pending"))
            db.execute(delete(EmailChallenge).where(
                EmailChallenge.email == email, EmailChallenge.created_at < now - 86400))
        # Reserve quota durably before SMTP; no connection/transaction during I/O.
        try:
            self.mailer.send_code(email, code, language, purpose)
        except (OSError, smtplib.SMTPException, ValueError):
            with self.store.session() as db:
                db.execute(update(EmailChallenge).where(EmailChallenge.id == challenge_id).values(state="failed"))
            raise AuthError("email_send_failed", 503) from None
        with self.store.session() as db:
            if user_id:
                db.get(User, user_id, with_for_update=True)
            email_lock(db, email)
            same_purpose = (EmailChallenge.email == email, EmailChallenge.purpose == purpose,
                            EmailChallenge.user_id.is_not_distinct_from(user_id))
            newer = db.scalar(select(EmailChallenge.id).where(
                *same_purpose, EmailChallenge.created_at > now, EmailChallenge.state.in_(("active", "used"))))
            pending = db.get(EmailChallenge, challenge_id)
            cancelled = not pending or pending.state != "pending"
            failed = bool(newer or int(time.time()) >= now + 600 or cancelled)
            if failed:
                if pending:
                    pending.state = "failed"
            else:
                db.execute(update(EmailChallenge).where(
                    *same_purpose, EmailChallenge.state == "active").values(state="superseded"))
                pending.state = "active"
        if failed:
            raise AuthError("email_send_failed", 503)
        return {"challenge_id": challenge_id, "expires_in": max(0, now + 600 - int(time.time())),
                "retry_after": max(0, now + 60 - int(time.time()))}

    def check(self, db: Session, challenge_id, code, purpose, email=None, user_id=None):
        """Return errors so failed-attempt counters commit before callers raise."""
        self.require_configured()
        challenge = db.get(EmailChallenge, challenge_id, with_for_update=True, populate_existing=True)
        if (not challenge or challenge.purpose != purpose or challenge.user_id != user_id
                or (email is not None and challenge.email != email) or challenge.state != "active"):
            return challenge, AuthError("invalid_code")
        if challenge.expires_at <= int(time.time()):
            return challenge, AuthError("code_expired")
        if challenge.attempts >= 5:
            return challenge, AuthError("code_attempts_exhausted")
        expected = self.digest(challenge_id, challenge.email, purpose, user_id, code)
        if not hmac.compare_digest(challenge.code_hash, expected):
            challenge.attempts += 1
            return challenge, AuthError("code_attempts_exhausted" if challenge.attempts == 5 else "invalid_code")
        return challenge, None
