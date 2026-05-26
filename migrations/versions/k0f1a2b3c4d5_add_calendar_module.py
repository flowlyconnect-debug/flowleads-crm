"""Add calendar module tables

Revision ID: k0f1a2b3c4d5
Revises: j0e1f2a3b4c5
Create Date: 2026-05-26 25:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "k0f1a2b3c4d5"
down_revision = "j0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calendar_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calendar_id", sa.String(length=255), nullable=True),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_calendar_connection_user_provider"),
    )
    with op.batch_alter_table("calendar_connections", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_calendar_connections_organization_id"), ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_calendar_connections_user_id"), ["user_id"], unique=False)

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("meeting_url", sa.String(length=1000), nullable=True),
        sa.Column("attendees", sa.JSON(), nullable=True),
        sa.Column("is_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="scheduled"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("calendar_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_calendar_events_external_event_id"), ["external_event_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_calendar_events_lead_id"), ["lead_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_calendar_events_organization_id"), ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_calendar_events_start_at"), ["start_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_calendar_events_user_id"), ["user_id"], unique=False)


def downgrade():
    op.drop_table("calendar_events")
    op.drop_table("calendar_connections")
