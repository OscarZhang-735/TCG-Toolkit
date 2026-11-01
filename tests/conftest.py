"""Real PostgreSQL tests, isolated from application and developer databases."""
import os
import uuid

from sqlalchemy.schema import CreateSchema, DropSchema
import pytest

from tcg_toolkit.database import Database, connection_info
from tcg_toolkit.db_cli import migrate


@pytest.fixture(autouse=True)
def postgres_schema(monkeypatch):
    if not os.environ.get("PGDATABASE", "").endswith("_test"):
        pytest.fail("Set PGDATABASE to a dedicated *_test database (see compose.test.yaml).")
    schema = "test_" + uuid.uuid4().hex
    conninfo = connection_info()
    with Database(conninfo) as database, database.connect() as db:
        db.execute(CreateSchema(schema))
    monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema}")
    migrate(conninfo)
    stores = []
    original = Database.__init__

    def track(self, *args, **kwargs):
        original(self, *args, **kwargs)
        stores.append(self)

    monkeypatch.setattr(Database, "__init__", track)
    # Some existing API tests use TestClient without a lifespan context.
    from tcg_toolkit.auth.routes import initialize_auth
    from tcg_toolkit.web import app
    initialize_auth(app)
    try:
        yield schema
    finally:
        for store in stores:
            store.close()
        with Database(conninfo) as database, database.connect() as db:
            db.execute(DropSchema(schema, cascade=True))
