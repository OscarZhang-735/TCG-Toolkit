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
            action = "注册账户" if purpose == "register" else "绑定邮箱"
            message["Subject"] = "TCG Toolkit 邮箱验证码"
            message.set_content(f"你正在{action}。\n\n验证码：{code}\n\n验证码在 10 分钟内有效，请勿向他人透露。\n如果不是你本人操作，请忽略此邮件。")
        else:
            action = "create an account" if purpose == "register" else "link your email"
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
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if user_id:
                user = db.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
                if not user or user["email"] is not None:
                    raise AuthError("email_already_bound", 409)
            if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise AuthError("email_taken", 409)
            recent = db.execute("""SELECT created_at FROM email_challenges
                WHERE email = ? AND created_at > ? ORDER BY created_at""", (email, now - 3600)).fetchall()
            retry = max(0, recent[-1][0] + 60 - now) if recent else 0
            if len(recent) >= 5:
                retry = max(retry, recent[-5][0] + 3600 - now)
            if retry:
                raise AuthError("rate_limited", 429, retry)
            # Reserving before SMTP enforces limits across concurrent requests/restarts.
            db.execute("""INSERT INTO email_challenges
                (id,email,purpose,user_id,code_hash,created_at,expires_at,state)
                VALUES (?,?,?,?,?,?,?,'pending')""",
                (challenge_id, email, purpose, user_id, digest, now, now + 600))
            db.execute("DELETE FROM email_challenges WHERE created_at < ?", (now - 86400,))
        try:
            self.mailer.send_code(email, code, language, purpose)
        except (OSError, smtplib.SMTPException, ValueError):
            with self.store.connect() as db:
                db.execute("UPDATE email_challenges SET state='failed' WHERE id=?", (challenge_id,))
            raise AuthError("email_send_failed", 503) from None
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A delayed delivery must not replace a newer, successfully sent code.
            newer = db.execute("""SELECT 1 FROM email_challenges WHERE email=? AND purpose=?
                AND user_id IS ? AND created_at>? AND state IN ('active','used')""",
                (email, purpose, user_id, now)).fetchone()
            if newer or int(time.time()) >= now + 600:
                db.execute("UPDATE email_challenges SET state='failed' WHERE id=?", (challenge_id,))
            else:
                db.execute("""UPDATE email_challenges SET state='superseded'
                    WHERE email=? AND purpose=? AND user_id IS ? AND state='active'""",
                    (email, purpose, user_id))
                db.execute("UPDATE email_challenges SET state='active' WHERE id=?", (challenge_id,))
        if newer or int(time.time()) >= now + 600:
            raise AuthError("email_send_failed", 503)
        return {"challenge_id": challenge_id, "expires_in": max(0, now + 600 - int(time.time())),
                "retry_after": max(0, now + 60 - int(time.time()))}

    def check(self, db, challenge_id, code, purpose, email=None, user_id=None):
        """Return an error so callers can commit failed-attempt counters before raising."""
        self.require_configured()
        row = db.execute("SELECT * FROM email_challenges WHERE id=?", (challenge_id,)).fetchone()
        if (not row or row["purpose"] != purpose or row["user_id"] != user_id
                or (email is not None and row["email"] != email) or row["state"] != "active"):
            return row, AuthError("invalid_code")
        if row["expires_at"] <= int(time.time()):
            return row, AuthError("code_expired")
        if row["attempts"] >= 5:
            return row, AuthError("code_attempts_exhausted")
        expected = self.digest(challenge_id, row["email"], purpose, user_id, code)
        if not hmac.compare_digest(row["code_hash"], expected):
            db.execute("UPDATE email_challenges SET attempts=attempts+1 WHERE id=?", (challenge_id,))
            return row, AuthError("code_attempts_exhausted" if row["attempts"] == 4 else "invalid_code")
        return row, None
