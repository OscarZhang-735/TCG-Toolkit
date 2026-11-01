"""PostgreSQL connections shared by account storage and currency caching."""
from contextlib import contextmanager
import os

from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def connection_info():
    required = ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD")
    if any(not os.getenv(name) for name in required):
        raise RuntimeError("Configure PGHOST, PGDATABASE, PGUSER and PGPASSWORD before starting.")
    return make_conninfo(host=os.environ["PGHOST"], port=os.getenv("PGPORT", "5432"),
                         dbname=os.environ["PGDATABASE"], user=os.environ["PGUSER"],
                         password=os.environ["PGPASSWORD"], connect_timeout=5)


class Database:
    def __init__(self, conninfo=None):
        self.conninfo = conninfo if conninfo is not None else connection_info()
        self.pool = ConnectionPool(self.conninfo, min_size=0, max_size=4, timeout=5,
                                   kwargs={"row_factory": dict_row},
                                   check=ConnectionPool.check_connection, open=True)

    @contextmanager
    def connect(self):
        with self.pool.connection() as db:
            yield db

    def close(self):
        self.pool.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def email_lock(db, email):
    # A mailbox may not have a user/challenge row yet. Transaction locks cover
    # that absent-row case as well as quota reservation and SMTP completion.
    db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("email:" + email,))
