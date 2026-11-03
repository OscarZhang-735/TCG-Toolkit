"""Account-scoped favorite groups and historical favorite assignment."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    groups = op.create_table(
        'favorite_groups',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('user_id', sa.Text, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.Text, nullable=False),
        sa.Column('is_default', sa.Boolean, nullable=False),
        sa.Column('created_at', sa.BigInteger, nullable=False),
        sa.UniqueConstraint('user_id', 'id', name='favorite_groups_user_id_id_key'),
        sa.UniqueConstraint('user_id', 'name', name='favorite_groups_user_name_key'),
    )
    op.create_index('favorite_groups_one_default', 'favorite_groups', ['user_id'],
                    unique=True, postgresql_where=groups.c.is_default)
    op.add_column('favorites', sa.Column('group_id', sa.Text, nullable=True))
    users = sa.table('users', sa.column('id', sa.Text), sa.column('created_at', sa.BigInteger))
    op.execute(groups.insert().from_select(
        ['id', 'user_id', 'name', 'is_default', 'created_at'],
        sa.select(sa.cast(sa.func.gen_random_uuid(), sa.Text), users.c.id,
                  sa.literal('默认收藏夹'), sa.true(), users.c.created_at)))
    favorites = sa.table('favorites', sa.column('user_id', sa.Text), sa.column('group_id', sa.Text))
    op.execute(favorites.update().where(favorites.c.user_id == groups.c.user_id)
               .values(group_id=groups.c.id))
    op.alter_column('favorites', 'group_id', nullable=False)
    op.create_foreign_key('favorites_user_group_fkey', 'favorites', 'favorite_groups',
                          ['user_id', 'group_id'], ['user_id', 'id'])
    op.create_index('favorites_user_group', 'favorites', ['user_id', 'group_id'])
    op.execute('INSERT INTO schema_migrations (version) VALUES (4)')


def downgrade():
    raise RuntimeError('Restore a matching backup to remove favorite groups.')
