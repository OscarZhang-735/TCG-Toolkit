"""Account-scoped graded card groups; existing graded cards remain ungrouped."""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'graded_card_groups',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('user_id', sa.Text, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.Text, nullable=False),
        sa.Column('created_at', sa.BigInteger, nullable=False),
        sa.UniqueConstraint('user_id', 'id', name='graded_card_groups_user_id_id_key'),
        sa.UniqueConstraint('user_id', 'name', name='graded_card_groups_user_name_key'),
    )
    op.add_column('graded_cards', sa.Column('group_id', sa.Text, nullable=True))
    op.create_foreign_key('graded_cards_user_group_fkey', 'graded_cards', 'graded_card_groups',
                          ['user_id', 'group_id'], ['user_id', 'id'])
    op.create_index('graded_cards_user_group', 'graded_cards', ['user_id', 'group_id'])
    op.execute('INSERT INTO schema_migrations (version) VALUES (18)')


def downgrade():
    raise RuntimeError('Graded card groups downgrade is not automatic; restore a matching backup.')
