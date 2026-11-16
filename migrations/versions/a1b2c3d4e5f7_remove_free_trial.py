"""Remove the free trial: trial organizations become monthly contracts.

Organizations still on "trial" are moved to "active" (plan flowleads) and the
trial columns are dropped. The downgrade restores the empty columns only.

Revision ID: a1b2c3d4e5f7
Revises: f8b9c0d1e2f3
"""

import sqlalchemy as sa
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
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("trial_reminder_sent_at")
        batch.drop_column("trial_ends_at")


def downgrade():
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("trial_reminder_sent_at", sa.DateTime(timezone=True), nullable=True))
