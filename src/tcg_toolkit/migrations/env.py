"""Alembic uses the application's connection configuration and ORM metadata."""
from alembic import context

from tcg_toolkit.database import Database
from tcg_toolkit.db_models import Base


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    with Database() as database, database.connect() as connection:
        run(connection)
