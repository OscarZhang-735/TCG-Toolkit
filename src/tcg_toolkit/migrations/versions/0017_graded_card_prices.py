"""Optional prices and their currencies for saved graded cards."""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('graded_cards', sa.Column('price', sa.Numeric(14, 2), nullable=True))
    op.add_column('graded_cards', sa.Column('currency', sa.Text, nullable=False, server_default=sa.text("'JPY'")))
    op.create_check_constraint('graded_cards_price_nonnegative', 'graded_cards', 'price >= 0')
    op.create_check_constraint('graded_cards_currency_known', 'graded_cards', "currency IN ('JPY', 'CNY', 'CAD')")
    op.execute('INSERT INTO schema_migrations (version) VALUES (17)')


def downgrade():
    raise RuntimeError('Graded card prices downgrade is not automatic; restore a matching backup.')
