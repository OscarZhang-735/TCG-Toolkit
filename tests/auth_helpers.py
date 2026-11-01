"""Real verification flows with an in-memory mail delivery boundary."""
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




def persisted_data(store):
    """Inspect all persisted application columns for request-scoped secrets."""
    from psycopg import sql
    contents = []
    with store.connect() as db:
        tables = db.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall()
        for table in tables:
            rows = db.execute(sql.SQL("SELECT row_to_json(t)::text AS value FROM {} t").format(
                sql.Identifier(table["tablename"])))
            contents.extend(row["value"] for row in rows)
    return "\n".join(contents).encode()
