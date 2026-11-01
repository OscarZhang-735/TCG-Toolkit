from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest
from fastapi.testclient import TestClient

from tcg_toolkit.auth.store import UserStore
from tcg_toolkit.database import connection_info
from tcg_toolkit import db_cli
from tcg_toolkit.web import app


@pytest.fixture
def empty_database():
    schema = "migration_" + uuid.uuid4().hex
    conninfo = connection_info()
    with psycopg.connect(conninfo, autocommit=True) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield make_conninfo(conninfo, options=f"-c search_path={schema}")
        finally:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_fresh_schema_is_explicit_and_concurrent_migrations_run_once(empty_database):
    with pytest.raises(RuntimeError, match="schema"):
        UserStore(empty_database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(db_cli.migrate, [empty_database] * 2)) == [1, 1]
    with UserStore(empty_database) as store, store.connect() as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()["count"] == 0
        assert db.execute("SELECT count(*) FROM schema_migrations").fetchone()["count"] == 1
        assert db.execute("SELECT to_regclass('yuyutei_credentials') AS name").fetchone()["name"] is None


def test_migration_rerun_preserves_data_and_sessions():
    with UserStore() as store, store.connect() as db:
        db.execute("INSERT INTO users (id,username,password_hash,created_at) VALUES ('u','alice','hash',1)")
        db.execute("INSERT INTO sessions (token_hash,user_id,expires_at) VALUES ('token','u',9999999999)")
    db_cli.migrate()
    with UserStore() as store, store.connect() as db:
        assert db.execute("SELECT user_id FROM sessions").fetchone()["user_id"] == 'u'


def test_newer_schema_is_rejected():
    with UserStore() as store, store.connect() as db:
        db.execute("INSERT INTO schema_migrations (version) VALUES (999)")
    with pytest.raises(RuntimeError, match="newer"):
        db_cli.migrate()
    with pytest.raises(RuntimeError, match="schema"):
        UserStore()


def test_failed_migration_rolls_back_schema_and_version(empty_database, tmp_path, monkeypatch):
    scripts = tmp_path / 'migrations'
    scripts.mkdir()
    script = scripts / '001_failure.sql'
    script.write_text('CREATE TABLE partial (id INTEGER); SELECT * FROM missing_table;')
    monkeypatch.setattr(db_cli, 'files', lambda _: tmp_path)
    with pytest.raises(psycopg.errors.UndefinedTable):
        db_cli.migrate(empty_database)
    with psycopg.connect(empty_database, row_factory=dict_row) as db:
        assert db_cli.version(db) == 0
        assert db.execute("SELECT to_regclass('partial') AS name").fetchone()["name"] is None


def test_health_detects_database_failure_and_recovers(monkeypatch):
    store = app.state.auth.store
    real_connect = store.connect

    @contextmanager
    def unavailable():
        raise psycopg.OperationalError('connection refused')
        yield

    client = TestClient(app)
    monkeypatch.setattr(store, 'connect', unavailable)
    assert client.get('/api/health').status_code == 503
    monkeypatch.setattr(store, 'connect', real_connect)
    assert client.get('/api/health').json() == {'status': 'ok'}


def test_schema_check_cannot_pass_without_database_tables():
    with app.state.auth.store.connect() as db:
        db.execute("UPDATE schema_migrations SET version=999")
    assert TestClient(app).get('/api/health').status_code == 503
