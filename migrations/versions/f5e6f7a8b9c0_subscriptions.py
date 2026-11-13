"""Subscription status, plan and trial of organizations.

Existing organizations stay "active" (billed by agreement, as before).

Revision ID: f5e6f7a8b9c0
Revises: f4d5e6f7a8b9
"""

import sqlalchemy as sa
from alembic import op

revision = "f5e6f7a8b9c0"
down_revision = "f4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("subscription_status", sa.String(length=20), nullable=False, server_default="active"))
        batch.add_column(sa.Column("plan", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("subscription_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("cancel_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("trial_reminder_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("trial_reminder_sent_at")
        batch.drop_column("cancel_at")
        batch.drop_column("subscription_started_at")
        batch.drop_column("trial_ends_at")
        batch.drop_column("plan")
        batch.drop_column("subscription_status")
