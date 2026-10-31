"""Account operations and revocable persistent sessions."""
import hashlib
import secrets
import time
import uuid
from .passwords import hasher, verify_password
from .email_verification import AuthError, EmailVerification, normalize_email

SESSION_SECONDS = 7 * 24 * 60 * 60


def public_user(row):
    return {**{key: row[key] for key in ("id", "username", "created_at", "email")},
            "email_verified": row["email_verified_at"] is not None}


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
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                   (token_hash(token), user_id, now + SESSION_SECONDS))
        return token

    def register(self, username, password, email, challenge_id, code):
        email = normalize_email(email)
        user = dict(id=str(uuid.uuid4()), username=username.lower(), created_at=int(time.time()),
                    email=email, email_verified_at=int(time.time()))
        encoded = hasher.hash(password)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            _, error = self.verification.check(db, challenge_id, code, "register", email=email)
            if error is None:
                if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                    raise DuplicateUsername()
                if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                    raise AuthError("email_taken", 409)
                db.execute("""INSERT INTO users
                    (id,username,password_hash,created_at,email,email_verified_at) VALUES (?,?,?,?,?,?)""",
                    (user["id"], user["username"], encoded, user["created_at"], email, user["email_verified_at"]))
                db.execute("UPDATE email_challenges SET state='used' WHERE id=?", (challenge_id,))
                token = self._session(db, user["id"])
        if error:
            raise error
        return public_user(user), token

    def send_binding_code(self, user_id, email, password, language):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not verify_password(row["password_hash"] if row else None, password):
            raise AuthError("invalid_password", 400)
        return self.verification.send(email, language, "bind", user_id)

    def bind_email(self, user_id, challenge_id, code):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row, error = self.verification.check(db, challenge_id, code, "bind", user_id=user_id)
            if error is None:
                user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                if user["email"] is not None:
                    raise AuthError("email_already_bound", 409)
                if db.execute("SELECT 1 FROM users WHERE email=?", (row["email"],)).fetchone():
                    raise AuthError("email_taken", 409)
                db.execute("UPDATE users SET email=?, email_verified_at=? WHERE id=?",
                           (row["email"], int(time.time()), user_id))
                db.execute("UPDATE email_challenges SET state='used' WHERE id=?", (challenge_id,))
                user = public_user(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        if error:
            raise error
        return user

    def login(self, username, password):
        username = username.strip()
        is_email = "@" in username
        if is_email:
            try:
                username = normalize_email(username)
            except ValueError:
                verify_password(None, password)
                return None
        with self.store.connect() as db:
            # Serialize password verification/session issuance with password changes.
            db.execute("BEGIN IMMEDIATE")
            query = ("SELECT * FROM users WHERE email = ? AND email_verified_at IS NOT NULL"
                     if is_email else "SELECT * FROM users WHERE username = ?")
            row = db.execute(query, (username,)).fetchone()
            if not verify_password(row["password_hash"] if row else None, password):
                return None
            if hasher.check_needs_rehash(row["password_hash"]):
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                           (hasher.hash(password), row["id"]))
            return public_user(row), self._session(db, row["id"])

    def send_password_code(self, user_id, language):
        with self.store.connect() as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or not user["email"] or user["email_verified_at"] is None:
            raise AuthError("verified_email_required")
        return self.verification.send(user["email"], language, "password_change", user_id)

    def _replace_password(self, db, user_id, encoded):
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (encoded, user_id))
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        # Also invalidate pending deliveries: SMTP completion must not reactivate them.
        db.execute("""UPDATE email_challenges SET state='superseded'
            WHERE user_id=? AND state IN ('pending','active')""", (user_id,))

    def change_password(self, user_id, token, new_password, method, current_password=None,
                        challenge_id=None, code=None):
        encoded = hasher.hash(new_password)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            session = db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND user_id=? AND expires_at>?",
                                 (token_hash(token), user_id, int(time.time()))).fetchone()
            if not session:
                raise AuthError("authentication_required", 401)
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            error = None
            if method == "password":
                if not verify_password(user["password_hash"], current_password):
                    error = AuthError("invalid_password")
            else:
                if not user["email"] or user["email_verified_at"] is None:
                    raise AuthError("verified_email_required")
                _, error = self.verification.check(db, challenge_id, code, "password_change",
                                                   email=user["email"], user_id=user_id)
            if error is None:
                if method == "email":
                    db.execute("UPDATE email_challenges SET state='used' WHERE id=?", (challenge_id,))
                self._replace_password(db, user_id, encoded)
        if error:
            raise error

    def reset_password(self, email, challenge_id, code, new_password):
        encoded = hasher.hash(new_password)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT * FROM users WHERE email=? AND email_verified_at IS NOT NULL",
                              (email,)).fetchone()
            if not user:
                raise AuthError("invalid_code")
            _, error = self.verification.check(db, challenge_id, code, "password_reset",
                                               email=email, user_id=user["id"])
            if error is None:
                db.execute("UPDATE email_challenges SET state='used' WHERE id=?", (challenge_id,))
                self._replace_password(db, user["id"], encoded)
        if error:
            raise error

    def current_user(self, token):
        if not token:
            return None
        with self.store.connect() as db:
            row = db.execute("""SELECT users.* FROM users JOIN sessions ON users.id = sessions.user_id
                WHERE token_hash = ? AND expires_at > ?""", (token_hash(token), time.time())).fetchone()
        return public_user(row) if row else None

    def logout(self, token):
        with self.store.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
