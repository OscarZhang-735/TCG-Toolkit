"""One query interval per user, reset to 24 hours for all existing accounts."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('subscription_interval_hours', sa.Integer,
                                    nullable=False, server_default='24'))
    op.create_check_constraint('users_subscription_interval_positive', 'users', 'subscription_interval_hours >= 1')
    op.execute('UPDATE subscriptions SET next_check_at = checked_at + 86400')
    op.drop_constraint('subscriptions_interval_hours_positive', 'subscriptions', type_='check')
    op.drop_column('subscriptions', 'interval_hours')
    op.execute('INSERT INTO schema_migrations (version) VALUES (10)')


def downgrade():
    raise RuntimeError('Subscription settings downgrade is not automatic; restore a matching backup.')
