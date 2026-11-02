"""Card subscriptions, daily checks and durable digest delivery."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("user_id", sa.Text, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("game", sa.Text, nullable=False),
        sa.Column("product_url", sa.Text, nullable=False),
        sa.Column("snapshot", sa.Text, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("checked_at", sa.BigInteger, nullable=False),
        sa.Column("last_error", sa.Text),
        sa.UniqueConstraint("user_id", "game", "product_url"),
    )
    op.create_table(
        "subscription_runs",
        sa.Column("local_date", sa.Date, primary_key=True),
        sa.Column("started_at", sa.BigInteger, nullable=False),
        sa.Column("finished_at", sa.BigInteger),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("last_error", sa.Text),
        sa.CheckConstraint("state IN ('running', 'complete', 'failed')"),
    )
    op.create_table(
        "subscription_emails",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("user_id", sa.Text, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_date", sa.Date, sa.ForeignKey("subscription_runs.local_date"), nullable=False),
        sa.Column("changes", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("sent_at", sa.BigInteger),
        sa.Column("last_error", sa.Text),
        sa.UniqueConstraint("user_id", "run_date"),
        sa.CheckConstraint("state IN ('pending', 'sent', 'failed', 'cancelled')"),
    )
    op.create_index("subscription_emails_due", "subscription_emails", ["state", "next_attempt_at"])
    op.execute("INSERT INTO schema_migrations (version) VALUES (2)")


def downgrade():
    raise RuntimeError("Subscription data removal is not automatic; restore a matching backup.")
