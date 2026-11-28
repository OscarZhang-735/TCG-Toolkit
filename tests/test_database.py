from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib.resources import files
import shutil
import uuid

from alembic.autogenerate import compare_metadata
from alembic import command
from alembic.runtime.migration import MigrationContext
from psycopg.conninfo import make_conninfo
from sqlalchemy import MetaData, Table, func, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.schema import CreateSchema, DropSchema
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.persistence.runtime import open_database
from tcg_toolkit.persistence.database import Database, connection_info
from tcg_toolkit.persistence.models import Base, Favorite, LoginSession, OwnedCard, SchemaMigration, User
from tcg_toolkit.persistence import schema
from tcg_toolkit.web import app

pytestmark = pytest.mark.usefixtures("postgres_schema")


@pytest.fixture
def empty_database():
    schema = "migration_" + uuid.uuid4().hex
    conninfo = connection_info()
    with Database(conninfo) as database:
        with database.connect() as db:
            db.execute(CreateSchema(schema))
        try:
            yield make_conninfo(conninfo, options=f"-c search_path={schema}")
        finally:
            with database.connect() as db:
                db.execute(DropSchema(schema, cascade=True))


def test_fresh_schema_is_explicit_and_concurrent_migrations_run_once(empty_database):
    with pytest.raises(RuntimeError, match="schema"):
        open_database(empty_database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(schema.migrate, [empty_database] * 2)) == [schema.SCHEMA_VERSION] * 2
    with open_database(empty_database) as store, store.session() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(SchemaMigration)) == schema.SCHEMA_VERSION
        assert not inspect(db.connection()).has_table('yuyutei_credentials')


def test_existing_pre_orm_schema_is_stamped_without_recreating_data(empty_database):
    with Database(empty_database) as store, store.connect() as db:
        # Exercise the exact immutable schema deployed before ORM adoption.
        SchemaMigration.__table__.create(db)
        db.exec_driver_sql(files('tcg_toolkit').joinpath('migrations', '001_initial.sql').read_text())
        db.execute(SchemaMigration.__table__.insert().values(version=1))
        db.execute(User.__table__.insert().values(id='legacy', username='alice', password_hash='hash', created_at=1))
        db.execute(LoginSession.__table__.insert().values(token_hash='legacy-token',user_id='legacy',expires_at=9999999999))
        db.execute(Favorite.__table__.insert().values(id='favorite', user_id='legacy', game='wsr',
                   product_url='https://yuyu-tei.jp/sell/wsr/card/OS01/1', snapshot='{}', created_at=1))
        db.execute(OwnedCard.__table__.insert().values(id='card', user_id='legacy', game='wsr',
                   card_number='A-001', rarity='R', name='Saved card', quantity=3, notes='keep',
                   game_key='wsr', number_key='a-001', rarity_key='r', created_at=1, updated_at=1))
    assert schema.migrate(empty_database) == schema.SCHEMA_VERSION
    with open_database(empty_database) as store, store.session() as db:
        user = db.get(User, 'legacy')
        assert (user.username, user.grading_lookup_date, user.grading_lookup_count) == ('alice', None, 0)
        assert db.get(LoginSession, 'legacy-token').user_id == 'legacy'
        assert db.get(Favorite, 'favorite').snapshot == '{}'
        assert db.get(Favorite, 'favorite').group_id is None
        assert db.get(OwnedCard, 'card').quantity == 3
        assert MigrationContext.configure(db.connection()).get_current_revision() == f'{schema.SCHEMA_VERSION:04d}'


def test_orm_metadata_matches_the_deployed_schema():
    with open_database() as store, store.connect() as db:
        assert compare_metadata(MigrationContext.configure(db), Base.metadata) == []


@pytest.mark.serial  # Delivers pending mail under the worker's database-wide lock.
def test_schema_eight_preserves_subscriptions_runs_and_pending_mail(empty_database):
    from datetime import date
    from tcg_toolkit.persistence.models import Subscription, SubscriptionRun, SubscriptionEmail
    from tcg_toolkit.subscriptions.worker import SubscriptionWorker
    from test_subscriptions import snapshot, Mailer
    import json

    day = date(2026, 11, 1)
    changes = json.dumps([dict(subscription_id='sub', before=snapshot(), after=snapshot(price=200), checked_at=1_800_000_000)])
    with Database(empty_database) as store, store.connect() as db:
        command.upgrade(schema.alembic_config(db), '0008')
        tables = {name: Table(name, MetaData(), autoload_with=db)
                  for name in ('users', 'subscriptions', 'subscription_runs', 'subscription_emails')}
        db.execute(tables['users'].insert().values(id='user', username='user', password_hash='hash', created_at=1,
                   email='user@example.com', email_verified_at=1))
        db.execute(tables['subscriptions'].insert().values(id='sub', user_id='user', game='wsr',
                   product_url=snapshot()['product_url'], snapshot=json.dumps(snapshot()), created_at=1,
                   checked_at=100, last_error='product_timeout'))
        db.execute(tables['subscription_runs'].insert().values(local_date=day, started_at=100,
                   finished_at=120, state='complete'))
        db.execute(tables['subscription_emails'].insert().values(id='mail', user_id='user', run_date=day,
                   changes=changes, state='pending', attempts=1, next_attempt_at=200, created_at=120,
                   last_error='OSError'))
    assert schema.migrate(empty_database) == schema.SCHEMA_VERSION
    with open_database(empty_database) as store:
        with store.session() as db:
            sub = db.get(Subscription, 'sub')
            assert (sub.next_check_at, sub.checked_at) == (86500, 100)
            assert db.get(User, 'user').subscription_interval_hours == 24
            assert sub.last_error == 'product_timeout' and sub.snapshot == json.dumps(snapshot())
            run = db.scalar(select(SubscriptionRun))
            assert str(uuid.UUID(run.id)) == run.id
            assert (run.local_date, run.started_at, run.finished_at, run.state) == (day, 100, 120, 'complete')
            mail = db.get(SubscriptionEmail, 'mail')
            assert (mail.run_id, mail.run_date, mail.changes) == (run.id, day, changes)
            assert (mail.state, mail.attempts, mail.next_attempt_at, mail.last_error) == ('pending', 1, 200, 'OSError')
        mailer = Mailer()
        SubscriptionWorker(store, mailer=mailer, clock=lambda: 200).deliver_due()
        assert len(mailer.messages) == 1
        with store.session() as db:
            assert db.get(SubscriptionEmail, 'mail').state == 'sent'


def test_newer_schema_is_rejected():
    with open_database() as store, store.session() as db:
        db.add(SchemaMigration(version=999))
    with pytest.raises(RuntimeError, match="newer"):
        schema.migrate()
    with pytest.raises(RuntimeError, match="schema"):
        open_database()


def test_failed_migration_rolls_back_schema_and_version(empty_database, tmp_path, monkeypatch):
    scripts = tmp_path / 'migrations'
    shutil.copytree(str(files('tcg_toolkit').joinpath('migrations')), scripts)
    next_revision = f'{schema.SCHEMA_VERSION + 1:04d}'
    (scripts / 'versions' / f'{next_revision}_failure.py').write_text(
        "from alembic import op\nimport sqlalchemy as sa\n"
        f"revision='{next_revision}'\ndown_revision='{schema.SCHEMA_VERSION:04d}'\n"
        "def upgrade():\n    op.create_table('partial',sa.Column('id',sa.Integer))\n"
        "    raise RuntimeError('simulated migration failure')\n")
    original = schema.alembic_config
    def config(connection):
        result = original(connection)
        result.set_main_option('script_location', str(scripts))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(schema, 'alembic_config', config)
        with pytest.raises(RuntimeError, match='simulated migration failure'):
            schema.migrate(empty_database)
    with Database(empty_database) as store, store.connect() as db:
        assert schema.version(db) == 0
        assert not inspect(db).has_table('partial')
        assert not inspect(db).has_table('alembic_version')
    assert schema.migrate(empty_database) == schema.SCHEMA_VERSION


def test_session_rolls_back_pending_and_flushed_changes():
    with open_database() as store:
        with pytest.raises(RuntimeError), store.session() as db:
            db.add(User(id='rollback',username='rollback',password_hash='hash',created_at=1))
            db.flush()
            raise RuntimeError('operation failed')
        with store.session() as db:
            assert db.get(User, 'rollback') is None
        with pytest.raises(IntegrityError), store.session() as db:
            db.add_all([User(id='one',username='same',password_hash='hash',created_at=1),
                        User(id='two',username='SAME',password_hash='hash',created_at=1)])
        with store.session() as db:
            assert db.scalar(select(func.count()).select_from(User)) == 0


def test_health_detects_database_failure_and_recovers(monkeypatch):
    @contextmanager
    def unavailable():
        raise OperationalError('connect', {}, Exception('connection refused'))
        yield

    with TestClient(app) as client:
        with monkeypatch.context() as patch:
            patch.setattr(app.state.database, 'connect', unavailable)
            assert client.get('/api/health').status_code == 503
        assert client.get('/api/health').json() == {'status': 'ok'}
