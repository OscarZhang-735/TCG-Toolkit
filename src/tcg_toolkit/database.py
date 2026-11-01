"""SQLAlchemy engine and transaction-scoped ORM sessions."""
from contextlib import contextmanager
import os

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


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
        self.engine = create_engine(
            "postgresql+psycopg://", connect_args=conninfo_to_dict(self.conninfo),
            pool_size=4, max_overflow=0, pool_timeout=5, pool_pre_ping=True,
            hide_parameters=True,
        )
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        """A new session per operation: commit on success, rollback on exception."""
        with self._sessions.begin() as session:
            yield session

    @contextmanager
    def connect(self):
        """SQLAlchemy connection for schema management; business code uses session()."""
        with self.engine.begin() as db:
            yield db

    def close(self):
        self.engine.dispose()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def email_lock(db: Session, email: str):
    # A mailbox may not have a user/challenge row yet. Transaction locks cover
    # that absent-row case as well as quota reservation and SMTP completion.
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("email:" + email, 0))))


def is_unique_violation(error, constraint=None):
    """Only translate expected unique conflicts; preserve other integrity errors."""
    return (getattr(error.orig, "sqlstate", None) == "23505"
            and (constraint is None or error.orig.diag.constraint_name == constraint))
