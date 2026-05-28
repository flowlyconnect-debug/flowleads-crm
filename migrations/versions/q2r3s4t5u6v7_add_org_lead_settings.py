"""Add org lead settings

Revision ID: q2r3s4t5u6v7
Revises: p1q2r3s4t5u6
Create Date: 2026-05-28 14:40:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "q2r3s4t5u6v7"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    op.create_table(
        "org_lead_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("default_pipeline_stage_id", sa.Integer(), nullable=True),
        sa.Column("default_owner_id", sa.Integer(), nullable=True),
        sa.Column("default_tags", json_type, nullable=False, server_default="[]"),
        sa.Column("last_lead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_lead_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["default_pipeline_stage_id"], ["pipeline_stages.id"]),
        sa.ForeignKeyConstraint(["default_owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id"),
    )
    with op.batch_alter_table("org_lead_settings", schema=None) as batch_op:
        batch_op.create_index(
            "ix_org_lead_settings_organization_id",
            ["organization_id"],
            unique=True,
        )


def downgrade():
    with op.batch_alter_table("org_lead_settings", schema=None) as batch_op:
        batch_op.drop_index("ix_org_lead_settings_organization_id")
    op.drop_table("org_lead_settings")
