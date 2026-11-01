"""Immutable baseline for PostgreSQL schema 1, including pre-ORM installations."""
from importlib.resources import files

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schema_migrations",
        sa.Column("version", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    # Preserve the already-deployed DDL as history; new revisions use Alembic
    # operations, never mutable Base.metadata.create_all().
    baseline = files("tcg_toolkit").joinpath("migrations", "001_initial.sql")
    op.get_bind().exec_driver_sql(baseline.read_text(encoding="utf-8"))
    op.bulk_insert(sa.table("schema_migrations", sa.column("version", sa.Integer)), [{"version": 1}])


def downgrade():
    raise RuntimeError("Baseline removal is not supported; restore a matching backup.")
