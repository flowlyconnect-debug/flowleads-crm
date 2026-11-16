"""Remove the free trial: trial organizations become monthly contracts.

Organizations still on "trial" are moved to "active" (plan flowleads). The
unused columns trial_ends_at and trial_reminder_sent_at stay in the table on
purpose: the previous release is still serving requests while this migration
runs (Render starts the new version next to the old one), and dropping columns
its model selects would break every page until the switch. Nothing reads them
any more; drop them in a later release.

Revision ID: a1b2c3d4e5f7
Revises: f8b9c0d1e2f3
"""

from alembic import op

revision = "a1b2c3d4e5f7"
down_revision = "f8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE organizations SET plan = COALESCE(plan, 'flowleads'), "
        "subscription_started_at = COALESCE(subscription_started_at, CURRENT_TIMESTAMP), "
        "subscription_status = 'active' WHERE subscription_status = 'trial'"
    )


def downgrade():
    pass  # data-only: former trials stay monthly contracts
