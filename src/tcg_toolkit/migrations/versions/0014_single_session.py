"""Keep one login session per account, including existing installations."""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    sessions = sa.table('sessions', sa.column('token_hash', sa.Text),
                        sa.column('user_id', sa.Text), sa.column('expires_at', sa.BigInteger))
    # Expiry is issuance time plus seven days. Resolve same-second logins
    # deterministically because older schema versions have no creation time.
    ranked = sa.select(sessions.c.token_hash, sa.func.row_number().over(
        partition_by=sessions.c.user_id,
        order_by=(sessions.c.expires_at.desc(), sessions.c.token_hash.desc()),
    ).label('position')).subquery()
    op.execute(sessions.delete().where(sessions.c.token_hash.in_(
        sa.select(ranked.c.token_hash).where(ranked.c.position > 1))))
    op.drop_index('sessions_user', table_name='sessions')
    op.create_index('sessions_user', 'sessions', ['user_id'], unique=True)
    op.execute('INSERT INTO schema_migrations (version) VALUES (14)')


def downgrade():
    raise RuntimeError('Single-session downgrade is not automatic; restore a matching backup.')
