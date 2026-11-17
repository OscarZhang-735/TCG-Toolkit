"""Optional user-chosen display names for saved graded cards."""
from alembic import op
import sqlalchemy as sa

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('graded_cards', sa.Column('display_name', sa.Text, nullable=True))
    op.execute('INSERT INTO schema_migrations (version) VALUES (19)')


def downgrade():
    raise RuntimeError('Graded card display names downgrade is not automatic; restore a matching backup.')
