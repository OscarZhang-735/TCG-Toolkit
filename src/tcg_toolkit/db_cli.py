"""Explicit Alembic migrations: tcg-db migrate | status | check."""
import argparse
import json

from alembic import command

from .database import Database
from . import schema


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "status", "check"))
    args = parser.parse_args(argv)
    if args.command == "migrate":
        current = schema.migrate()
    else:
        with Database() as database, database.connect() as db:
            current = schema.version(db)
            if args.command == "check":
                schema.require_schema(db)
                command.check(schema.alembic_config(db))
    print(json.dumps({"engine": "postgresql", "schema": current}))


if __name__ == "__main__":
    main()
