"""Optional manually recorded unit price for inventory cards."""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('owned_cards', sa.Column('price', sa.Numeric(14, 2), nullable=True))
    op.create_check_constraint('owned_cards_price_nonnegative', 'owned_cards', 'price >= 0')
    op.execute('INSERT INTO schema_migrations (version) VALUES (12)')


def downgrade():
    raise RuntimeError('Card price downgrade is not automatic; restore a matching backup.')
