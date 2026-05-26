"""Add email logs, templates, and organization email settings

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-26 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email_from_name", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("email_from_email", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("mailgun_domain", sa.String(length=255), nullable=True))

    op.create_table(
        "email_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("mailgun_message_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("email_logs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_email_logs_lead_id"), ["lead_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_email_logs_organization_id"), ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_email_logs_mailgun_message_id"), ["mailgun_message_id"], unique=False)

    op.create_table(
        "email_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("subject_template", sa.String(length=255), nullable=False),
        sa.Column("body_html_template", sa.Text(), nullable=False),
        sa.Column("body_text_template", sa.Text(), nullable=True),
        sa.Column("variables", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_email_template_org_name"),
    )
    with op.batch_alter_table("email_templates", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_email_templates_organization_id"), ["organization_id"], unique=False)

    # Seed system templates idempotently via app layer on next startup / first list.


def downgrade():
    op.drop_table("email_templates")
    op.drop_table("email_logs")
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.drop_column("mailgun_domain")
        batch_op.drop_column("email_from_email")
        batch_op.drop_column("email_from_name")
