"""Asiakasseuranta (usage_daily: daily counters per customer user),
Asiakastiedotteet (customer_announcements: e-mails sent to customers) and the
per-customer daily export limit (organizations.export_daily_limit).

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("export_daily_limit", sa.Integer(), nullable=True))
    op.create_table(
        "usage_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("organization_id", "user_id", "day", "metric", name="uq_usage_daily"),
    )
    op.create_index("ix_usage_daily_org_day", "usage_daily", ["organization_id", "day"])
    op.create_table(
        "customer_announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audience", sa.String(20), nullable=False, server_default="admins"),
        sa.Column("organization_ids", json_type, nullable=True),
        sa.Column("sent_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", json_type, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("customer_announcements")
    op.drop_index("ix_usage_daily_org_day", table_name="usage_daily")
    op.drop_table("usage_daily")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("export_daily_limit")
