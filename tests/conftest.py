"""Real PostgreSQL tests, isolated from application and developer databases."""
import os
import uuid

from sqlalchemy import text
from sqlalchemy.schema import CreateSchema, DropSchema
import pytest

from tcg_toolkit.persistence.database import Database, connection_info
from tcg_toolkit.persistence.schema import migrate


@pytest.fixture(scope="session")
def migrated_schema():
    """Migrate one schema per session; per-test isolation is done by truncation.

    Migration itself stays covered by test_database.py, which migrates fresh
    empty schemas explicitly.
    """
    if not os.environ.get("PGDATABASE", "").endswith("_test"):
        pytest.fail("Set PGDATABASE to a dedicated *_test database (see compose.test.yaml).")
    schema = "test_" + uuid.uuid4().hex
    conninfo = connection_info()
    with Database(conninfo) as database, database.connect() as db:
        db.execute(CreateSchema(schema))
    seed = schema + "_seed"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PGOPTIONS", f"-c search_path={schema}")
        migrate(conninfo)
    with Database(conninfo) as database, database.connect() as db:
        tables = sorted(row[0] for row in db.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = :schema"), {"schema": schema}))
        # Snapshot rows written by migrations (version bookkeeping, seed data) so
        # each test starts from exactly the freshly migrated state.
        db.execute(CreateSchema(seed))
        seeded = []
        for name in tables:
            if db.scalar(text(f"SELECT EXISTS (SELECT 1 FROM {schema}.{name})")):
                db.execute(text(f"CREATE TABLE {seed}.{name} AS TABLE {schema}.{name}"))
                seeded.append(name)
    reset = [f"TRUNCATE {', '.join(f'{schema}.{name}' for name in tables)} RESTART IDENTITY CASCADE"]
    reset += [f"INSERT INTO {schema}.{name} SELECT * FROM {seed}.{name}" for name in seeded]
    try:
        yield schema, reset
    finally:
        with Database(conninfo) as database, database.connect() as db:
            db.execute(DropSchema(seed, cascade=True))
            db.execute(DropSchema(schema, cascade=True))


@pytest.fixture
def postgres_schema(monkeypatch, migrated_schema):
    schema, reset = migrated_schema
    monkeypatch.setenv('TCG_SUBSCRIPTIONS_ENABLED', 'false')
    monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema}")
    try:
        yield schema
    finally:
        with Database(connection_info()) as database, database.connect() as db:
            # Fail loudly instead of hanging if a test leaked an open transaction.
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            for statement in reset:
                db.execute(text(statement))
