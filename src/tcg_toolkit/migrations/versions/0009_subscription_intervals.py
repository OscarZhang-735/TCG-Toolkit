"""Per-subscription schedules and multiple digest runs per day."""
import uuid

from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('subscriptions', sa.Column('interval_hours', sa.Integer, nullable=False, server_default='24'))
    op.add_column('subscriptions', sa.Column('next_check_at', sa.BigInteger))
    op.execute('UPDATE subscriptions SET next_check_at = checked_at + 86400')
    op.alter_column('subscriptions', 'next_check_at', nullable=False)
    op.create_check_constraint('subscriptions_interval_hours_positive', 'subscriptions', 'interval_hours >= 1')
    op.create_index('subscriptions_due', 'subscriptions', ['next_check_at'])

    op.add_column('subscription_runs', sa.Column('id', sa.Text))
    runs = sa.table('subscription_runs', sa.column('local_date', sa.Date), sa.column('id', sa.Text))
    connection = op.get_bind()
    for day in connection.scalars(sa.select(runs.c.local_date)).all():
        connection.execute(runs.update().where(runs.c.local_date == day).values(id=str(uuid.uuid4())))
    op.alter_column('subscription_runs', 'id', nullable=False)
    op.add_column('subscription_emails', sa.Column('run_id', sa.Text))
    op.execute('UPDATE subscription_emails e SET run_id = r.id FROM subscription_runs r WHERE e.run_date = r.local_date')
    op.alter_column('subscription_emails', 'run_id', nullable=False)
    op.drop_constraint('subscription_emails_run_date_fkey', 'subscription_emails', type_='foreignkey')
    op.drop_constraint('subscription_emails_user_id_run_date_key', 'subscription_emails', type_='unique')
    op.drop_constraint('subscription_runs_pkey', 'subscription_runs', type_='primary')
    op.create_primary_key('subscription_runs_pkey', 'subscription_runs', ['id'])
    op.create_foreign_key('subscription_emails_run_id_fkey', 'subscription_emails', 'subscription_runs', ['run_id'], ['id'])
    op.create_unique_constraint('subscription_emails_user_id_run_id_key', 'subscription_emails', ['user_id', 'run_id'])
    op.execute('INSERT INTO schema_migrations (version) VALUES (9)')


def downgrade():
    raise RuntimeError('Subscription schedule downgrade is not automatic; restore a matching backup.')
