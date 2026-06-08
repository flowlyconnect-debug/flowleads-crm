"""Ensure org_lead_settings columns exist

Revision ID: x6y7z8a9b0c1
Revises: w5x6y7z8a9b0
Create Date: 2026-06-08 15:10:00.000000

Idempotent fix for production DBs where org_lead_settings exists but is
missing default_industry / default_region (partial or manual schema).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "x6y7z8a9b0c1"
down_revision = "w5x6y7z8a9b0"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _table_names() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _ensure_org_lead_settings_table() -> None:
    if "org_lead_settings" in _table_names():
        return

    op.create_table(
        "org_lead_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("default_pipeline_stage_id", sa.Integer(), nullable=True),
        sa.Column("default_owner_id", sa.Integer(), nullable=True),
        sa.Column("default_tags", json_type, nullable=False, server_default="[]"),
        sa.Column("default_industry", sa.String(length=100), nullable=True),
        sa.Column("default_region", sa.String(length=100), nullable=True),
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


def upgrade():
    _ensure_org_lead_settings_table()
    if "org_lead_settings" not in _table_names():
        return

    columns = _column_names("org_lead_settings")
    if "default_industry" not in columns:
        op.add_column(
            "org_lead_settings",
            sa.Column("default_industry", sa.String(length=100), nullable=True),
        )
    if "default_region" not in columns:
        op.add_column(
            "org_lead_settings",
            sa.Column("default_region", sa.String(length=100), nullable=True),
        )


def downgrade():
    if "org_lead_settings" not in _table_names():
        return

    columns = _column_names("org_lead_settings")
    if "default_region" in columns:
        op.drop_column("org_lead_settings", "default_region")
    if "default_industry" in columns:
        op.drop_column("org_lead_settings", "default_industry")
