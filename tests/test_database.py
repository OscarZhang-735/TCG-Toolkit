from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib.resources import files
import ast
import shutil
import uuid

from alembic.autogenerate import compare_metadata
from alembic import command
from alembic.runtime.migration import MigrationContext
from psycopg.conninfo import make_conninfo
from sqlalchemy import MetaData, Table, event, func, inspect, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.schema import CreateSchema, DropSchema
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.database import Database, connection_info
from tcg_toolkit.db_models import Base, CardGroup, Favorite, FavoriteGroup, LoginSession, OwnedCard, SchemaMigration, User
from tcg_toolkit import db_cli
from tcg_toolkit.web import app


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
        UserStore(empty_database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(db_cli.migrate, [empty_database] * 2)) == [db_cli.SCHEMA_VERSION] * 2
    with UserStore(empty_database) as store, store.session() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(SchemaMigration)) == db_cli.SCHEMA_VERSION
        assert not inspect(db.connection()).has_table('yuyutei_credentials')


def test_migration_rerun_preserves_data_and_sessions():
    with UserStore() as store, store.session() as db:
        db.add(User(id='u',username='alice',password_hash='hash',created_at=1))
        db.flush()
        db.add(LoginSession(token_hash='token',user_id='u',expires_at=9999999999))
    db_cli.migrate()
    with UserStore() as store, store.session() as db:
        assert db.get(LoginSession, 'token').user_id == 'u'


def test_schema_two_upgrade_defaults_to_existing_registered_email(empty_database):
    with Database(empty_database) as store, store.connect() as db:
        command.upgrade(db_cli.alembic_config(db), '0002')
        db.execute(User.__table__.insert().values(id='existing', username='existing', password_hash='hash',
                   created_at=1, email='existing@example.com', email_verified_at=1))
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    from tcg_toolkit.notification_email import notification_recipient
    with UserStore(empty_database) as store, store.session() as db:
        user = db.get(User, 'existing')
        assert user.notification_email is None and user.notification_email_verified_at is None
        assert notification_recipient(user) == 'existing@example.com'


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
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store, store.session() as db:
        assert db.get(User, 'legacy').username == 'alice'
        assert db.get(LoginSession, 'legacy-token').user_id == 'legacy'
        assert db.get(Favorite, 'favorite').snapshot == '{}'
        assert db.get(Favorite, 'favorite').group_id is None
        assert db.get(OwnedCard, 'card').quantity == 3
        assert MigrationContext.configure(db.connection()).get_current_revision() == '0013'


def test_orm_metadata_matches_the_deployed_schema():
    with app.state.auth.store.connect() as db:
        assert compare_metadata(MigrationContext.configure(db), Base.metadata) == []


def test_schema_ten_keeps_historical_cards_ungrouped(empty_database):
    with Database(empty_database) as store, store.connect() as db:
        command.upgrade(db_cli.alembic_config(db), '0010')
        db.execute(User.__table__.insert(), [dict(id=user, username=user, password_hash='hash', created_at=1)
                                             for user in ('alice', 'bob', 'empty')])
        old_cards = Table('owned_cards', MetaData(), autoload_with=db)
        db.execute(old_cards.insert(), [
            dict(id=f'{user}-{i}', user_id=user, game='WS', card_number=f'ABC-{i}', rarity='SR',
                 name='历史卡牌', quantity=i, notes='Keep', image_url='https://example.com/card.png',
                 game_key='ws', number_key=f'abc-{i}', rarity_key='sr', created_at=10+i, updated_at=20+i)
            for user in ('alice', 'bob') for i in range(2)])
        before = [dict(row) for row in db.execute(select(old_cards).order_by(old_cards.c.id)).mappings()]
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store, store.session() as db:
        assert db.scalar(select(func.count()).select_from(CardGroup)) == 0
        after = [dict(row) for row in db.execute(select(OwnedCard.__table__).order_by(OwnedCard.id)).mappings()]
        for row in after:
            assert row.pop('group_id') is None
            assert row.pop('price') is None
            assert row.pop('currency') == 'JPY'
        assert after == before
        db.add(CardGroup(id='deck', user_id='alice', name='Deck', created_at=1))
        db.flush()
        db.get(OwnedCard, 'alice-0').group_id = 'deck'
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store, store.session() as db:
        assert db.get(OwnedCard, 'alice-0').group_id == 'deck'


def test_schema_eleven_adds_optional_price_preserving_cards(empty_database):
    from decimal import Decimal

    with Database(empty_database) as store, store.connect() as db:
        command.upgrade(db_cli.alembic_config(db), '0011')
        db.execute(User.__table__.insert().values(id='alice', username='alice', password_hash='hash', created_at=1))
        db.execute(CardGroup.__table__.insert().values(id='deck', user_id='alice', name='Deck', created_at=1))
        old_cards = Table('owned_cards', MetaData(), autoload_with=db)
        db.execute(old_cards.insert().values(id='card', user_id='alice', group_id='deck', game='WS',
                   card_number='ABC-001', rarity='SR', name='Saved', quantity=3, notes='Keep',
                   game_key='ws', number_key='abc-001', rarity_key='sr', created_at=10, updated_at=20))
        before = dict(db.execute(select(old_cards)).mappings().one())
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store, store.session() as db:
        after = dict(db.execute(select(OwnedCard.__table__)).mappings().one())
        assert after.pop('price') is None
        assert after.pop('currency') == 'JPY'
        assert after == before
        db.get(OwnedCard, 'card').price = Decimal('12.34')
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store, store.session() as db:
        assert db.get(OwnedCard, 'card').price == Decimal('12.34')


def test_schema_twelve_records_existing_prices_as_jpy(empty_database):
    from decimal import Decimal

    with Database(empty_database) as store, store.connect() as db:
        command.upgrade(db_cli.alembic_config(db), '0012')
        db.execute(User.__table__.insert().values(id='alice', username='alice', password_hash='hash', created_at=1))
        old_cards = Table('owned_cards', MetaData(), autoload_with=db)
        db.execute(old_cards.insert(), [
            dict(id=f'card-{i}', user_id='alice', game='WS', card_number=f'ABC-{i}', rarity='SR', name='Saved',
                 quantity=1, price=price, notes='', game_key='ws', number_key=f'abc-{i}', rarity_key='sr',
                 created_at=10, updated_at=20) for i, price in enumerate([None, Decimal('800.50')])])
        before = [dict(row) for row in db.execute(select(old_cards).order_by(old_cards.c.id)).mappings()]
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store, store.session() as db:
        after = [dict(row) for row in db.execute(select(OwnedCard.__table__).order_by(OwnedCard.id)).mappings()]
        assert [row.pop('currency') for row in after] == ['JPY', 'JPY']
        assert after == before
        db.get(OwnedCard, 'card-1').currency = 'CNY'
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store, store.session() as db:
        assert db.get(OwnedCard, 'card-1').currency == 'CNY'


def test_schema_eight_preserves_subscriptions_runs_and_pending_mail(empty_database):
    from datetime import date
    from tcg_toolkit.db_models import Subscription, SubscriptionRun, SubscriptionEmail
    from tcg_toolkit.subscription_worker import SubscriptionWorker
    from test_subscriptions import snapshot, Mailer
    import json

    day = date(2026, 11, 1)
    changes = json.dumps([dict(subscription_id='sub', before=snapshot(), after=snapshot(price=200), checked_at=1_800_000_000)])
    with Database(empty_database) as store, store.connect() as db:
        command.upgrade(db_cli.alembic_config(db), '0008')
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
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store:
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
    with UserStore() as store, store.session() as db:
        db.add(SchemaMigration(version=999))
    with pytest.raises(RuntimeError, match="newer"):
        db_cli.migrate()
    with pytest.raises(RuntimeError, match="schema"):
        UserStore()


def test_schema_nine_resets_all_accounts_to_24_hours(empty_database):
    from tcg_toolkit.db_models import Subscription
    with Database(empty_database) as store, store.connect() as db:
        command.upgrade(db_cli.alembic_config(db), '0009')
        users = Table('users', MetaData(), autoload_with=db)
        subscriptions = Table('subscriptions', MetaData(), autoload_with=db)
        db.execute(users.insert(), [dict(id=u, username=u, password_hash='hash', created_at=1)
                                    for u in ('mixed', 'uniform', 'empty')])
        for number, (user, interval) in enumerate([('mixed', 1), ('mixed', 6), ('uniform', 3)]):
            db.execute(subscriptions.insert().values(id=str(number), user_id=user, game='wsr',
                       product_url=f'https://example.com/{number}', snapshot='{}', created_at=1,
                       checked_at=100, interval_hours=interval, next_check_at=100 + interval * 3600))
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store, store.session() as db:
        assert [u.subscription_interval_hours for u in db.scalars(select(User))] == [24, 24, 24]
        assert [s.next_check_at for s in db.scalars(select(Subscription))] == [86500] * 3
        assert 'interval_hours' not in {c['name'] for c in inspect(db.connection()).get_columns('subscriptions')}


def test_failed_migration_rolls_back_schema_and_version(empty_database, tmp_path, monkeypatch):
    scripts = tmp_path / 'migrations'
    shutil.copytree(str(files('tcg_toolkit').joinpath('migrations')), scripts)
    (scripts / 'versions' / '0014_failure.py').write_text(
        "from alembic import op\nimport sqlalchemy as sa\n"
        "revision='0014'\ndown_revision='0013'\n"
        "def upgrade():\n    op.create_table('partial',sa.Column('id',sa.Integer))\n"
        "    raise RuntimeError('simulated migration failure')\n")
    original = db_cli.alembic_config
    def config(connection):
        result = original(connection)
        result.set_main_option('script_location', str(scripts))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(db_cli, 'alembic_config', config)
        with pytest.raises(RuntimeError, match='simulated migration failure'):
            db_cli.migrate(empty_database)
    with Database(empty_database) as store, store.connect() as db:
        assert db_cli.version(db) == 0
        assert not inspect(db).has_table('partial')
        assert not inspect(db).has_table('alembic_version')
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION


def test_session_rolls_back_pending_and_flushed_changes():
    store = app.state.auth.store
    with pytest.raises(RuntimeError):
        with store.session() as db:
            db.add(User(id='rollback',username='rollback',password_hash='hash',created_at=1))
            db.flush()
            raise RuntimeError('operation failed')
    with store.session() as db:
        assert db.get(User, 'rollback') is None
    with pytest.raises(IntegrityError):
        with store.session() as db:
            db.add_all([User(id='one',username='same',password_hash='hash',created_at=1),
                        User(id='two',username='SAME',password_hash='hash',created_at=1)])
    with store.session() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_values_are_bound_parameters_not_sql():
    store = app.state.auth.store
    payload = "quoted' ; DROP TABLE users; --"
    captured = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))
    event.listen(store.engine, 'before_cursor_execute', capture)
    try:
        with store.session() as db:
            db.add(User(id='bound',username=payload,password_hash='hash',created_at=1))
        with store.session() as db:
            assert db.scalar(select(User).where(User.username == payload)).id == 'bound'
    finally:
        event.remove(store.engine, 'before_cursor_execute', capture)
    assert all(payload not in statement for statement, _ in captured)
    assert any(payload in parameters.values() for _, parameters in captured if isinstance(parameters, dict))


def test_business_code_has_no_raw_sql_escape_hatches():
    root = files('tcg_toolkit')
    for path in root.rglob('*.py'):
        if 'migrations' in path.relative_to(root).parts:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, 'attr', getattr(node.func, 'id', ''))
            assert name not in ('text', 'literal_column', 'exec_driver_sql'), str(path)
            if name == 'execute' and node.args:
                assert not isinstance(node.args[0], (ast.Constant, ast.JoinedStr)), str(path)


def test_health_detects_database_failure_and_recovers(monkeypatch):
    store = app.state.auth.store
    real_connect = store.connect
    @contextmanager
    def unavailable():
        raise OperationalError('connect', {}, Exception('connection refused'))
        yield
    client = TestClient(app)
    monkeypatch.setattr(store, 'connect', unavailable)
    assert client.get('/api/health').status_code == 503
    monkeypatch.setattr(store, 'connect', real_connect)
    assert client.get('/api/health').json() == {'status': 'ok'}


def test_schema_check_cannot_pass_without_database_tables():
    with app.state.auth.store.session() as db:
        db.execute(update(SchemaMigration).where(SchemaMigration.version == db_cli.SCHEMA_VERSION).values(version=999))
    assert TestClient(app).get('/api/health').status_code == 503


def test_schema_three_keeps_historical_favorites_ungrouped(empty_database):
    with Database(empty_database) as store, store.connect() as db:
        command.upgrade(db_cli.alembic_config(db), '0003')
        db.execute(User.__table__.insert(), [
            dict(id=user, username=user, password_hash='hash', created_at=1)
            for user in ('alice', 'bob', 'empty')])
        old_favorites = Table('favorites', MetaData(), autoload_with=db)
        db.execute(old_favorites.insert(), [
            dict(id=f'{user}-{i}', user_id=user, game='wsr', product_url=f'https://example/{i}',
                 snapshot='{"price":123}', created_at=10+i, refreshed_at=20+i)
            for user in ('alice', 'bob') for i in range(2)])
        before = [dict(row) for row in db.execute(select(old_favorites).order_by(old_favorites.c.id)).mappings()]
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store, store.session() as db:
        assert db.scalar(select(func.count()).select_from(FavoriteGroup)) == 0
        after = [dict(row) for row in db.execute(select(Favorite.__table__).order_by(Favorite.id)).mappings()]
        for row in after:
            assert row.pop('group_id') is None
        assert after == before
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION


def test_schema_four_default_groups_become_ungrouped(empty_database):
    with Database(empty_database) as store, store.connect() as db:
        command.upgrade(db_cli.alembic_config(db), '0003')
        db.execute(User.__table__.insert(), [dict(id=user, username=user, password_hash='hash', created_at=1)
                                             for user in ('alice', 'bob')])
        old_favorites = Table('favorites', MetaData(), autoload_with=db)
        db.execute(old_favorites.insert(), [
            dict(id=f'{user}-{i}', user_id=user, game='wsr', product_url=f'https://example/{i}',
                 snapshot='{}', created_at=10+i) for user in ('alice', 'bob') for i in range(2)])
        command.upgrade(db_cli.alembic_config(db), '0004')
        old_groups = Table('favorite_groups', MetaData(), autoload_with=db)
        db.execute(old_groups.insert().values(id='deck', user_id='alice', name='Deck', is_default=False, created_at=5))
        grouped = Table('favorites', MetaData(), autoload_with=db)
        db.execute(grouped.update().where(grouped.c.id == 'alice-1').values(group_id='deck'))
    assert db_cli.migrate(empty_database) == db_cli.SCHEMA_VERSION
    with UserStore(empty_database) as store, store.session() as db:
        assert [(row.id, row.name) for row in db.scalars(select(FavoriteGroup))] == [('deck', 'Deck')]
        assert {row.id: row.group_id for row in db.scalars(select(Favorite))} == {
            'alice-0': None, 'alice-1': 'deck', 'bob-0': None, 'bob-1': None}
        assert 'is_default' not in {column['name'] for column in inspect(db.connection()).get_columns('favorite_groups')}
