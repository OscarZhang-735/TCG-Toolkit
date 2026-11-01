"""Account storage backed by the shared PostgreSQL connection pool."""
from ..database import Database
from ..db_cli import require_schema


class UserStore(Database):
    def __init__(self, conninfo=None):
        super().__init__(conninfo)
        try:
            with self.connect() as db:
                require_schema(db)
        except Exception:
            self.close()
            raise
