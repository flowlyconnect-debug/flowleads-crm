"""Saved searches on the Kohteet page.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    op.create_table(
        "saved_target_searches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("params", json_type, nullable=False),
        sa.Column("notify", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_saved_target_searches_organization_id", "saved_target_searches", ["organization_id"])
    op.create_index("ix_saved_target_searches_user_id", "saved_target_searches", ["user_id"])


def downgrade():
    op.drop_index("ix_saved_target_searches_user_id", table_name="saved_target_searches")
    op.drop_index("ix_saved_target_searches_organization_id", table_name="saved_target_searches")
    op.drop_table("saved_target_searches")
