"""Add lead streams

Revision ID: p1q2r3s4t5u6
Revises: o1p2q3r4s5t6
Create Date: 2026-05-28 12:15:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p1q2r3s4t5u6"
down_revision = "o1p2q3r4s5t6"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    op.create_table(
        "lead_streams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("source_match", sa.String(length=100), nullable=True),
        sa.Column("segment_key", sa.String(length=100), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("pipeline_stage_id", sa.Integer(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("default_tags", json_type, nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_lead_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lead_count", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["pipeline_stage_id"], ["pipeline_stages.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("lead_streams", schema=None) as batch_op:
        batch_op.create_index(
            "ix_lead_streams_org_active", ["organization_id", "is_active"], unique=False
        )
        batch_op.create_index(
            "ix_lead_streams_org_source", ["organization_id", "source_match"], unique=False
        )


def downgrade():
    with op.batch_alter_table("lead_streams", schema=None) as batch_op:
        batch_op.drop_index("ix_lead_streams_org_source")
        batch_op.drop_index("ix_lead_streams_org_active")
    op.drop_table("lead_streams")
