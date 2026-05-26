"""Add email sequences and lead unsubscribed flag

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-05-26 22:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "h8c9d0e1f2a3"
down_revision = "g7b8c9d0e1f2"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("unsubscribed", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.create_table(
        "email_sequences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trigger_type", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("trigger_config", json_type, nullable=True),
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
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_email_sequence_org_name"),
    )
    with op.batch_alter_table("email_sequences", schema=None) as batch_op:
        batch_op.create_index("ix_email_sequences_organization_id", ["organization_id"])

    op.create_table(
        "email_sequence_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sequence_id", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delay_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delay_hours", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subject_template", sa.String(length=500), nullable=False),
        sa.Column("body_html_template", sa.Text(), nullable=True),
        sa.Column("body_text_template", sa.Text(), nullable=True),
        sa.Column("condition", json_type, nullable=True),
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
        sa.ForeignKeyConstraint(["sequence_id"], ["email_sequences.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("email_sequence_steps", schema=None) as batch_op:
        batch_op.create_index("ix_email_sequence_steps_sequence_id", ["sequence_id"])

    op.create_table(
        "email_sequence_enrollments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sequence_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("enrolled_by", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("current_step_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_send_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "enrolled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["sequence_id"], ["email_sequences.id"]),
        sa.ForeignKeyConstraint(["enrolled_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("email_sequence_enrollments", schema=None) as batch_op:
        batch_op.create_index("ix_email_sequence_enrollments_sequence_id", ["sequence_id"])
        batch_op.create_index("ix_email_sequence_enrollments_lead_id", ["lead_id"])
        batch_op.create_index("ix_email_sequence_enrollments_organization_id", ["organization_id"])
        batch_op.create_index("ix_email_sequence_enrollments_next_send_at", ["next_send_at"])

    op.create_table(
        "email_sequence_sents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enrollment_id", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("email_log_id", sa.Integer(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["email_log_id"], ["email_logs.id"]),
        sa.ForeignKeyConstraint(["enrollment_id"], ["email_sequence_enrollments.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["step_id"], ["email_sequence_steps.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("email_sequence_sents", schema=None) as batch_op:
        batch_op.create_index("ix_email_sequence_sents_enrollment_id", ["enrollment_id"])
        batch_op.create_index("ix_email_sequence_sents_lead_id", ["lead_id"])


def downgrade():
    op.drop_table("email_sequence_sents")
    op.drop_table("email_sequence_enrollments")
    op.drop_table("email_sequence_steps")
    op.drop_table("email_sequences")
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.drop_column("unsubscribed")
