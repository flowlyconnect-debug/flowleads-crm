"""Add GDPR module fields and data export requests

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-05-26 24:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "j0e1f2a3b4c5"
down_revision = "i9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("gdpr_consent", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("gdpr_consent_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("gdpr_consent_source", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("gdpr_legal_basis", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("marketing_opt_in", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("is_anonymized", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True)
        )

    with op.batch_alter_table("organization_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("gdpr_default_legal_basis", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "gdpr_retention_days",
                sa.Integer(),
                nullable=False,
                server_default="730",
            )
        )
        batch_op.add_column(
            sa.Column(
                "gdpr_auto_anonymize_inactive",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("privacy_policy_url", sa.String(length=500), nullable=True)
        )
        batch_op.add_column(
            sa.Column("data_controller_name", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("data_controller_email", sa.String(length=255), nullable=True)
        )

    with op.batch_alter_table("email_logs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("gdpr_legal_basis", sa.String(length=64), nullable=True)
        )

    op.create_table(
        "data_export_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("export_type", sa.String(length=32), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("download_token", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("download_token"),
    )
    with op.batch_alter_table("data_export_requests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_data_export_requests_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_data_export_requests_requested_by"),
            ["requested_by"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_data_export_requests_status"),
            ["status"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_data_export_requests_lead_id"),
            ["lead_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_data_export_requests_download_token"),
            ["download_token"],
            unique=True,
        )


def downgrade():
    op.drop_table("data_export_requests")
    with op.batch_alter_table("email_logs", schema=None) as batch_op:
        batch_op.drop_column("gdpr_legal_basis")
    with op.batch_alter_table("organization_settings", schema=None) as batch_op:
        batch_op.drop_column("data_controller_email")
        batch_op.drop_column("data_controller_name")
        batch_op.drop_column("privacy_policy_url")
        batch_op.drop_column("gdpr_auto_anonymize_inactive")
        batch_op.drop_column("gdpr_retention_days")
        batch_op.drop_column("gdpr_default_legal_basis")
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.drop_column("anonymized_at")
        batch_op.drop_column("is_anonymized")
        batch_op.drop_column("marketing_opt_in")
        batch_op.drop_column("gdpr_legal_basis")
        batch_op.drop_column("gdpr_consent_source")
        batch_op.drop_column("gdpr_consent_at")
        batch_op.drop_column("gdpr_consent")
        batch_op.drop_column("unsubscribed_at")
