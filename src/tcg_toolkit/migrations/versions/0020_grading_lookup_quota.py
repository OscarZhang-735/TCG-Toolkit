"""Store the current UTC day's paid certificate lookup count per account."""
from alembic import op
import sqlalchemy as sa

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('grading_lookup_date', sa.Date, nullable=True))
    op.add_column('users', sa.Column('grading_lookup_count', sa.Integer, nullable=False, server_default='0'))
    op.execute('INSERT INTO schema_migrations (version) VALUES (20)')


def downgrade():
    raise RuntimeError('Grading lookup quota downgrade is not automatic; restore a matching backup.')
