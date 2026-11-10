"""Add notification-dedup columns (stale-alert cooldown, failed-job alert)

Revision ID: a9b0c1d2e3f4
Revises: z8a9b0c1d2e3
Create Date: 2026-11-10 20:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "a9b0c1d2e3f4"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "lead_streams",
        sa.Column("last_alert_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "org_lead_settings",
        sa.Column("last_alert_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "org_lead_settings",
        sa.Column(
            "daily_digest_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "org_lead_settings",
        sa.Column("last_digest_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "search_jobs",
        sa.Column("failure_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("search_jobs", "failure_notified_at")
    op.drop_column("org_lead_settings", "last_digest_sent_at")
    op.drop_column("org_lead_settings", "daily_digest_enabled")
    op.drop_column("org_lead_settings", "last_alert_sent_at")
    op.drop_column("lead_streams", "last_alert_sent_at")
