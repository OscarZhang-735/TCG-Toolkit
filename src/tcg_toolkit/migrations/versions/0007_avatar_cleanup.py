"""Persist COS avatar deletions until they succeed."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('avatar_cleanup',
                    sa.Column('url', sa.Text(), primary_key=True),
                    sa.Column('next_attempt_at', sa.BigInteger(), nullable=False))
    op.execute('INSERT INTO schema_migrations (version) VALUES (7)')


def downgrade():
    raise RuntimeError('Restore a matching backup to roll back avatar cleanup.')
