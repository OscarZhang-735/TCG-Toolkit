"""Explicit Alembic migrations: tcg-db migrate | status | check."""
import argparse
from importlib.resources import files
import json

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, inspect, select

from .database import Database
from .db_models import SchemaMigration

SCHEMA_VERSION = 10


def alembic_config(connection):
    config = Config()
    config.set_main_option("script_location", str(files("tcg_toolkit").joinpath("migrations")))
    config.attributes["connection"] = connection
    return config


def version(db):
    if not inspect(db).has_table(SchemaMigration.__tablename__):
        return 0
    return db.scalar(select(func.coalesce(func.max(SchemaMigration.version), 0)))


def require_schema(db):
    current = MigrationContext.configure(db).get_current_revision()
    head = ScriptDirectory.from_config(alembic_config(db)).get_current_head()
    if version(db) != SCHEMA_VERSION or current != head:
        raise RuntimeError("Database schema does not match this application; run tcg-db migrate with the matching release.")


def migrate(conninfo=None):
    with Database(conninfo) as database, database.connect() as db:
        db.execute(select(func.pg_advisory_xact_lock(894210611)))
        current = version(db)
        if current > SCHEMA_VERSION:
            raise RuntimeError("Database schema is newer than this application.")
        config = alembic_config(db)
        revision = MigrationContext.configure(db).get_current_revision()
        if revision is None and current == 1:
            # Existing PostgreSQL schema 1 already contains the baseline tables.
            # Stamp in the same transaction; never recreate or overwrite data.
            command.stamp(config, "0001")
        elif revision is None and current != 0:
            raise RuntimeError("Unrecognized legacy schema; migration requires a matching release.")
        command.upgrade(config, "head")
        require_schema(db)
    return SCHEMA_VERSION


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "status", "check"))
    args = parser.parse_args(argv)
    if args.command == "migrate":
        current = migrate()
    else:
        with Database() as database, database.connect() as db:
            current = version(db)
            if args.command == "check":
                require_schema(db)
                command.check(alembic_config(db))
    print(json.dumps({"engine": "postgresql", "schema": current}))


if __name__ == "__main__":
    main()
