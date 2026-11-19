"""Open the application database and check it against the deployed schema."""
from .database import Database
from .schema import require_schema


def open_database(conninfo=None):
    database = Database(conninfo)
    try:
        with database.connect() as connection:
            require_schema(connection)
    except BaseException:
        database.close()
        raise
    return database
