"""Account-scoped graded card certificates, each with its latest lookup snapshot."""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'graded_cards',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('user_id', sa.Text, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('provider', sa.Text, nullable=False),
        sa.Column('cert_number', sa.Text, nullable=False),
        sa.Column('snapshot', sa.Text, nullable=False),
        sa.Column('created_at', sa.BigInteger, nullable=False),
        sa.Column('refreshed_at', sa.BigInteger, nullable=False),
        sa.Column('sort_id', sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.UniqueConstraint('user_id', 'provider', 'cert_number', name='graded_cards_user_cert_key'),
    )
    op.execute('INSERT INTO schema_migrations (version) VALUES (15)')


def downgrade():
    raise RuntimeError('Graded cards downgrade is not automatic; restore a matching backup.')
