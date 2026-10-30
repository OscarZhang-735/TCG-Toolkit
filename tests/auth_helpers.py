"""Real verification flows with an in-memory mail delivery boundary."""
import sqlite3
from tcg_toolkit.auth.email_verification import EmailVerification
from tcg_toolkit.web import app

TEST_SECRET = 'test-email-secret-with-at-least-32-bytes'


class Mailbox:
    configured = True

    def __init__(self):
        self.messages = []
        self.failure = None

    def send_code(self, email, code, language, purpose):
        if self.failure:
            raise self.failure
        self.messages.append(dict(email=email, code=code, language=language, purpose=purpose))


def install_mailbox():
    mailbox = Mailbox()
    app.state.auth.verification = EmailVerification(app.state.auth.store, mailbox, TEST_SECRET)
    return mailbox


def registration_body(client, username='alice', password='password123', email=None):
    verification = app.state.auth.verification
    if not isinstance(verification.mailer, Mailbox):
        install_mailbox()
    email = email or f'{username.lower()}@example.com'
    response = client.post('/api/auth/register/email-code', json={'email': email})
    assert response.status_code == 200, response.text
    return dict(username=username, password=password, confirm_password=password, email=email,
                challenge_id=response.json()['challenge_id'],
                code=app.state.auth.verification.mailer.messages[-1]['code'])


def register_user(client, username='alice', password='password123', email=None):
    return client.post('/api/auth/register', json=registration_body(client, username, password, email))


def create_v6_database(path):
    """Construct the old schema directly, without downgrading a current database."""
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
            CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at INTEGER NOT NULL);
            CREATE INDEX sessions_expiry ON sessions(expires_at);
            CREATE TABLE favorites (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                game TEXT NOT NULL, product_url TEXT NOT NULL, snapshot TEXT NOT NULL,
                created_at INTEGER NOT NULL, refreshed_at INTEGER, UNIQUE(user_id, game, product_url));
            CREATE TABLE owned_cards (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                game TEXT NOT NULL, card_number TEXT NOT NULL, rarity TEXT NOT NULL, name TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity >= 0), notes TEXT NOT NULL,
                game_key TEXT NOT NULL, number_key TEXT NOT NULL, rarity_key TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                image_url TEXT NOT NULL DEFAULT '', UNIQUE(user_id,game_key,number_key,rarity_key));
            CREATE TABLE cart_operations (user_id TEXT NOT NULL REFERENCES users(id), operation_key TEXT NOT NULL,
                request_hash TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('processing','complete')),
                status_code INTEGER, response_json TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY(user_id,operation_key));
            PRAGMA user_version=6;
        ''')
