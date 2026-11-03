"""Favorites start ungrouped: drop per-user default groups in favor of a NULL group."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('favorites', 'group_id', nullable=True)
    groups = sa.table('favorite_groups', sa.column('id', sa.Text), sa.column('user_id', sa.Text),
                      sa.column('is_default', sa.Boolean))
    favorites = sa.table('favorites', sa.column('user_id', sa.Text), sa.column('group_id', sa.Text))
    op.execute(favorites.update().where(favorites.c.group_id.in_(sa.select(groups.c.id).where(groups.c.is_default)))
               .values(group_id=None))
    op.execute(groups.delete().where(groups.c.is_default))
    op.drop_index('favorite_groups_one_default', table_name='favorite_groups')
    op.drop_column('favorite_groups', 'is_default')
    op.execute('INSERT INTO schema_migrations (version) VALUES (5)')


def downgrade():
    raise RuntimeError('Restore a matching backup to bring back default favorite groups.')
