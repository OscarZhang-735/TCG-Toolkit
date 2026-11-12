"""Currency of the manually recorded unit price; existing cards are JPY."""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('owned_cards', sa.Column('currency', sa.Text, nullable=False, server_default=sa.text("'JPY'")))
    op.create_check_constraint('owned_cards_currency_known', 'owned_cards', "currency IN ('JPY', 'CNY', 'CAD')")
    op.execute('INSERT INTO schema_migrations (version) VALUES (13)')


def downgrade():
    raise RuntimeError('Card currency downgrade is not automatic; restore a matching backup.')
