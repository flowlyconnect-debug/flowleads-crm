"""Add web forms module

Revision ID: o1p2q3r4s5t6
Revises: n0p1q2r3s4t5
Create Date: 2026-05-27 08:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "o1p2q3r4s5t6"
down_revision = "n0p1q2r3s4t5"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    op.create_table(
        "web_forms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("form_token", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("submit_button_text", sa.String(length=100), nullable=False, server_default="Lähetä"),
        sa.Column(
            "success_message",
            sa.String(length=500),
            nullable=False,
            server_default="Kiitos! Otamme yhteyttä pian.",
        ),
        sa.Column("fields", json_type, nullable=False, server_default="[]"),
        sa.Column("default_stage_id", sa.Integer(), nullable=True),
        sa.Column("default_assigned_to", sa.Integer(), nullable=True),
        sa.Column("auto_enroll_sequence_id", sa.Integer(), nullable=True),
        sa.Column("notify_users", json_type, nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("submission_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["default_stage_id"], ["pipeline_stages.id"]),
        sa.ForeignKeyConstraint(["default_assigned_to"], ["users.id"]),
        sa.ForeignKeyConstraint(["auto_enroll_sequence_id"], ["email_sequences.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("web_forms", schema=None) as batch_op:
        batch_op.create_index("ix_web_forms_organization_id", ["organization_id"], unique=False)
        batch_op.create_index("ix_web_forms_form_token", ["form_token"], unique=True)

    op.create_table(
        "web_form_submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("form_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("raw_data", json_type, nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="processed"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["form_id"], ["web_forms.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("web_form_submissions", schema=None) as batch_op:
        batch_op.create_index("ix_web_form_submissions_form_id", ["form_id"], unique=False)
        batch_op.create_index(
            "ix_web_form_submissions_organization_id", ["organization_id"], unique=False
        )
        batch_op.create_index("ix_web_form_submissions_status", ["status"], unique=False)


def downgrade():
    with op.batch_alter_table("web_form_submissions", schema=None) as batch_op:
        batch_op.drop_index("ix_web_form_submissions_status")
        batch_op.drop_index("ix_web_form_submissions_organization_id")
        batch_op.drop_index("ix_web_form_submissions_form_id")
    op.drop_table("web_form_submissions")

    with op.batch_alter_table("web_forms", schema=None) as batch_op:
        batch_op.drop_index("ix_web_forms_form_token")
        batch_op.drop_index("ix_web_forms_organization_id")
    op.drop_table("web_forms")
