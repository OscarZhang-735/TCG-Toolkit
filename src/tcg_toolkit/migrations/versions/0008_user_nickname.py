"""Add an optional display nickname to user accounts."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('nickname', sa.Text(), nullable=True))
    op.execute('INSERT INTO schema_migrations (version) VALUES (8)')


def downgrade():
    raise RuntimeError('Restore a matching backup to roll back user nicknames.')
