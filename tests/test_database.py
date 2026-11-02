from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib.resources import files
import ast
import shutil
import uuid

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from psycopg.conninfo import make_conninfo
from sqlalchemy import event, func, inspect, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.schema import CreateSchema, DropSchema
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.database import Database, connection_info
from tcg_toolkit.db_models import Base, Favorite, LoginSession, OwnedCard, SchemaMigration, User
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
        assert list(pool.map(db_cli.migrate, [empty_database] * 2)) == [2, 2]
    with UserStore(empty_database) as store, store.session() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(SchemaMigration)) == 2
        assert not inspect(db.connection()).has_table('yuyutei_credentials')


def test_migration_rerun_preserves_data_and_sessions():
    with UserStore() as store, store.session() as db:
        db.add(User(id='u',username='alice',password_hash='hash',created_at=1))
        db.flush()
        db.add(LoginSession(token_hash='token',user_id='u',expires_at=9999999999))
    db_cli.migrate()
    with UserStore() as store, store.session() as db:
        assert db.get(LoginSession, 'token').user_id == 'u'


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
    assert db_cli.migrate(empty_database) == 2
    with UserStore(empty_database) as store, store.session() as db:
        assert db.get(User, 'legacy').username == 'alice'
        assert db.get(LoginSession, 'legacy-token').user_id == 'legacy'
        assert db.get(Favorite, 'favorite').snapshot == '{}'
        assert db.get(OwnedCard, 'card').quantity == 3
        assert MigrationContext.configure(db.connection()).get_current_revision() == '0002'


def test_orm_metadata_matches_the_deployed_schema():
    with app.state.auth.store.connect() as db:
        assert compare_metadata(MigrationContext.configure(db), Base.metadata) == []


def test_newer_schema_is_rejected():
    with UserStore() as store, store.session() as db:
        db.add(SchemaMigration(version=999))
    with pytest.raises(RuntimeError, match="newer"):
        db_cli.migrate()
    with pytest.raises(RuntimeError, match="schema"):
        UserStore()


def test_failed_migration_rolls_back_schema_and_version(empty_database, tmp_path, monkeypatch):
    scripts = tmp_path / 'migrations'
    shutil.copytree(str(files('tcg_toolkit').joinpath('migrations')), scripts)
    (scripts / 'versions' / '0003_failure.py').write_text(
        "from alembic import op\nimport sqlalchemy as sa\n"
        "revision='0003'\ndown_revision='0002'\n"
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
    assert db_cli.migrate(empty_database) == 2


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
        db.execute(update(SchemaMigration).where(SchemaMigration.version == 2).values(version=999))
    assert TestClient(app).get('/api/health').status_code == 503
