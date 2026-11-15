"""User-supplied front and back images for graded cards, kept apart from lookup snapshots."""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('graded_cards', sa.Column('front_image_url', sa.Text, nullable=True))
    op.add_column('graded_cards', sa.Column('back_image_url', sa.Text, nullable=True))
    op.execute('INSERT INTO schema_migrations (version) VALUES (16)')


def downgrade():
    raise RuntimeError('Graded card images downgrade is not automatic; restore a matching backup.')
