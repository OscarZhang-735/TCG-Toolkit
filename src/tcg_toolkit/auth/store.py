"""Versioned SQLite user and session persistence."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class UserStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 8:
                raise RuntimeError("User database is newer than this application.")
            if version == 0:
                db.execute("""CREATE TABLE users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL, created_at INTEGER NOT NULL)""")
                db.execute("""CREATE TABLE sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at INTEGER NOT NULL)""")
                db.execute("CREATE INDEX sessions_expiry ON sessions(expires_at)")
                db.execute("PRAGMA user_version = 1")
            if version < 2:
                db.execute("""CREATE TABLE favorites (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    game TEXT NOT NULL, product_url TEXT NOT NULL,
                    snapshot TEXT NOT NULL, created_at INTEGER NOT NULL,
                    refreshed_at INTEGER,
                    UNIQUE(user_id, game, product_url))""")
                db.execute("PRAGMA user_version = 2")
            if version < 3:
                db.execute("""CREATE TABLE owned_cards (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    game TEXT NOT NULL, card_number TEXT NOT NULL, rarity TEXT NOT NULL,
                    name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0),
                    notes TEXT NOT NULL, game_key TEXT NOT NULL,
                    number_key TEXT NOT NULL, rarity_key TEXT NOT NULL,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    UNIQUE(user_id, game_key, number_key, rarity_key))""")
                db.execute("PRAGMA user_version = 3")

            if version < 4:
                db.execute("ALTER TABLE owned_cards ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")
                db.execute("PRAGMA user_version = 4")

            if version < 6:
                db.execute("""CREATE TABLE cart_operations (
                    user_id TEXT NOT NULL REFERENCES users(id),
                    operation_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('processing', 'complete')),
                    status_code INTEGER, response_json TEXT,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    PRIMARY KEY(user_id, operation_key))""")
                db.execute("PRAGMA user_version = 6")

            if version < 7:
                db.execute("PRAGMA secure_delete = ON")
                db.execute("DROP TABLE IF EXISTS yuyutei_credentials")
        if version < 7:
            # Remove previously freed credential pages as well as current rows.
            # External backups are not modified by this migration.
            with self.connect() as db:
                db.execute("VACUUM")
                if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    raise RuntimeError("Credential cleanup is waiting for database readers; retry startup.")
                # Keep version 6 until cleanup succeeds, so an interrupted or
                # failed cleanup is retried on the next startup.
                db.execute("BEGIN IMMEDIATE")
                if db.execute("PRAGMA user_version").fetchone()[0] < 7:
                    db.execute("PRAGMA user_version = 7")

        # Re-read under the write lock: another startup may have migrated already.
        # v7's secret cleanup must finish before recording any later version.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("PRAGMA user_version").fetchone()[0] < 8:
                db.execute("ALTER TABLE users ADD COLUMN email TEXT")
                db.execute("ALTER TABLE users ADD COLUMN email_verified_at INTEGER")
                db.execute("CREATE UNIQUE INDEX users_email ON users(email)")
                db.execute("""CREATE TABLE email_challenges (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL, purpose TEXT NOT NULL,
                    user_id TEXT REFERENCES users(id), code_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL CHECK(state IN
                        ('pending', 'active', 'failed', 'superseded', 'used')))
                """)
                db.execute("CREATE INDEX email_challenges_quota ON email_challenges(email, created_at)")
                db.execute("PRAGMA user_version = 8")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()
