"""Verified notification address independent of account identity."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('notification_email', sa.Text, nullable=True))
    op.add_column('users', sa.Column('notification_email_verified_at', sa.BigInteger, nullable=True))
    op.execute('INSERT INTO schema_migrations (version) VALUES (3)')


def downgrade():
    raise RuntimeError('Restore a matching backup to remove notification email settings.')
