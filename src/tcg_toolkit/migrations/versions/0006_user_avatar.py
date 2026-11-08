"""Store each user's current avatar URL."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('avatar_url', sa.Text(), nullable=True))
    op.execute('INSERT INTO schema_migrations (version) VALUES (6)')


def downgrade():
    op.drop_column('users', 'avatar_url')
    op.execute('DELETE FROM schema_migrations WHERE version = 6')
