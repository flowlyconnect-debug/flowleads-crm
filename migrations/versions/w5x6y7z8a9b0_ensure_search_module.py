"""Ensure search module tables and columns exist

Revision ID: w5x6y7z8a9b0
Revises: v4w5x6y7z8a9
Create Date: 2026-06-08 13:40:00.000000

Idempotent safety migration for production DBs missing search_profiles,
search_jobs, or search_dedupe after partial/skipped deploys.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "w5x6y7z8a9b0"
down_revision = "v4w5x6y7z8a9"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _table_names() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _ensure_search_profiles() -> None:
    if "search_profiles" not in _table_names():
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
        return

    columns = _column_names("search_profiles")
    additions = {
        "source": sa.Column("source", sa.String(length=50), nullable=False, server_default="oikotie"),
        "crm_api_key": sa.Column("crm_api_key", sa.String(length=200), nullable=True),
        "schedule_description": sa.Column(
            "schedule_description",
            sa.String(length=50),
            nullable=False,
            server_default="daily",
        ),
        "is_active": sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        "last_run_at": sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        "total_runs": sa.Column("total_runs", sa.Integer(), nullable=False, server_default="0"),
        "total_leads_sent": sa.Column(
            "total_leads_sent",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        "created_at": sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        "updated_at": sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("search_profiles", column)


def _ensure_search_jobs() -> None:
    if "search_jobs" not in _table_names():
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
        return

    columns = _column_names("search_jobs")
    additions = {
        "started_at": sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        "completed_at": sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        "leads_found": sa.Column("leads_found", sa.Integer(), nullable=False, server_default="0"),
        "leads_sent": sa.Column("leads_sent", sa.Integer(), nullable=False, server_default="0"),
        "error_message": sa.Column("error_message", sa.Text(), nullable=True),
        "retry_count": sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        "created_at": sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("search_jobs", column)


def _ensure_search_dedupe() -> None:
    if "search_dedupe" not in _table_names():
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


def upgrade():
    _ensure_search_profiles()
    _ensure_search_jobs()
    _ensure_search_dedupe()


def downgrade():
    pass
