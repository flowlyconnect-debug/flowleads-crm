"""Add search profiles, jobs, and dedupe tables

Revision ID: v4w5x6y7z8a9
Revises: u3v4w5x6y7z8
Create Date: 2026-06-02 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v4w5x6y7z8a9"
down_revision = "u3v4w5x6y7z8"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    op.create_table(
        "search_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("remonttityyppi", sa.String(length=100), nullable=False),
        sa.Column("regions", json_type, nullable=False, server_default="[]"),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="oikotie"),
        sa.Column("crm_api_key", sa.String(length=200), nullable=True),
        sa.Column(
            "schedule_description",
            sa.String(length=50),
            nullable=False,
            server_default="daily",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_leads_sent", sa.Integer(), nullable=False, server_default="0"),
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
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("search_profiles", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_search_profiles_organization_id"),
            ["organization_id"],
            unique=False,
        )

    op.create_table(
        "search_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("search_profile_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leads_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("leads_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["search_profile_id"], ["search_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("search_jobs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_search_jobs_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_search_jobs_search_profile_id"),
            ["search_profile_id"],
            unique=False,
        )

    op.create_table(
        "search_dedupe",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "source_id",
            name="uq_search_dedupe_org_source_id",
        ),
    )
    with op.batch_alter_table("search_dedupe", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_search_dedupe_organization_id"),
            ["organization_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("search_dedupe", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_search_dedupe_organization_id"))
    op.drop_table("search_dedupe")

    with op.batch_alter_table("search_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_search_jobs_search_profile_id"))
        batch_op.drop_index(batch_op.f("ix_search_jobs_organization_id"))
    op.drop_table("search_jobs")

    with op.batch_alter_table("search_profiles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_search_profiles_organization_id"))
    op.drop_table("search_profiles")
