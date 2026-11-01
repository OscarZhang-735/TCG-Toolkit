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
    """Inspect mapped application columns for request-scoped secrets."""
    import json
    from sqlalchemy import select
    from tcg_toolkit.db_models import Base
    contents = []
    with store.session() as db:
        for table in Base.metadata.sorted_tables:
            contents.extend(dict(row) for row in db.execute(select(table)).mappings())
    return json.dumps(contents, default=str).encode()
