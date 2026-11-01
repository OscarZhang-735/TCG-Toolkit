"""Explicit, transactional schema migrations: tcg-db migrate | status."""
import argparse
from importlib.resources import files
import json

import psycopg
from psycopg.rows import dict_row

from .database import connection_info

SCHEMA_VERSION = 1


def version(db):
    if db.execute("SELECT to_regclass('schema_migrations') AS name").fetchone()["name"] is None:
        return 0
    return db.execute("SELECT COALESCE(max(version), 0) AS version FROM schema_migrations").fetchone()["version"]


def require_schema(db):
    if version(db) != SCHEMA_VERSION:
        raise RuntimeError("Database schema does not match this application; run tcg-db migrate with the matching release.")


def migrate(conninfo=None):
    with psycopg.connect(conninfo if conninfo is not None else connection_info(), row_factory=dict_row) as db:
        db.execute("SELECT pg_advisory_xact_lock(894210611)")
        current = version(db)
        if current > SCHEMA_VERSION:
            raise RuntimeError("Database schema is newer than this application.")
        db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        for number in range(current + 1, SCHEMA_VERSION + 1):
            scripts = sorted(p for p in files("tcg_toolkit").joinpath("migrations").iterdir()
                             if p.name.startswith(f"{number:03}_") and p.name.endswith(".sql"))
            if len(scripts) != 1:
                raise RuntimeError(f"Expected exactly one migration for version {number}.")
            db.execute(scripts[0].read_text(encoding="utf-8"))
            db.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (number,))
    return SCHEMA_VERSION


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "status"))
    args = parser.parse_args(argv)
    if args.command == "migrate":
        current = migrate()
    else:
        with psycopg.connect(connection_info(), row_factory=dict_row) as db:
            current = version(db)
    print(json.dumps({"engine": "postgresql", "schema": current}))


if __name__ == "__main__":
    main()
