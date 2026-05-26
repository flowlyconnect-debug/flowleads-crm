"""Add tasks and organization settings

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-26 18:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organization_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("auto_task_on_new_lead", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_task_no_contact_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("auto_task_stage_change", sa.Boolean(), nullable=False, server_default=sa.false()),
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
        sa.UniqueConstraint("organization_id"),
    )
    with op.batch_alter_table("organization_settings", schema=None) as batch_op:
        batch_op.create_index("ix_organization_settings_organization_id", ["organization_id"], unique=True)

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("assigned_to", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
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
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.create_index("ix_tasks_organization_id", ["organization_id"], unique=False)
        batch_op.create_index("ix_tasks_lead_id", ["lead_id"], unique=False)
        batch_op.create_index("ix_tasks_assigned_to", ["assigned_to"], unique=False)
        batch_op.create_index("ix_tasks_due_date", ["due_date"], unique=False)
        batch_op.create_index(
            "ix_tasks_org_assigned_status",
            ["organization_id", "assigned_to", "status"],
            unique=False,
        )
        batch_op.create_index(
            "ix_tasks_reminder_pending",
            ["reminder_at", "reminder_sent", "status"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index("ix_tasks_reminder_pending")
        batch_op.drop_index("ix_tasks_org_assigned_status")
        batch_op.drop_index("ix_tasks_due_date")
        batch_op.drop_index("ix_tasks_assigned_to")
        batch_op.drop_index("ix_tasks_lead_id")
        batch_op.drop_index("ix_tasks_organization_id")
    op.drop_table("tasks")

    with op.batch_alter_table("organization_settings", schema=None) as batch_op:
        batch_op.drop_index("ix_organization_settings_organization_id")
    op.drop_table("organization_settings")
