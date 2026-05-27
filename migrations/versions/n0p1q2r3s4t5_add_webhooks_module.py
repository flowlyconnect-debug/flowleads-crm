"""Add webhooks module

Revision ID: n0p1q2r3s4t5
Revises: i9d0e1f2a3b4
Create Date: 2026-05-27 06:30:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "n0p1q2r3s4t5"
down_revision = "m1n2o3p4q5r6"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("url_encrypted", sa.String(length=2048), nullable=False),
        sa.Column("secret_encrypted", sa.String(length=1024), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("events", json_type, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("webhook_endpoints", schema=None) as batch_op:
        batch_op.create_index(
            "ix_webhook_endpoints_organization_id", ["organization_id"], unique=False
        )
        batch_op.create_index(
            "ix_webhook_endpoints_provider", ["provider"], unique=False
        )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("endpoint_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["endpoint_id"], ["webhook_endpoints.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("webhook_deliveries", schema=None) as batch_op:
        batch_op.create_index(
            "ix_webhook_deliveries_endpoint_id", ["endpoint_id"], unique=False
        )
        batch_op.create_index(
            "ix_webhook_deliveries_organization_id", ["organization_id"], unique=False
        )
        batch_op.create_index(
            "ix_webhook_deliveries_event_type", ["event_type"], unique=False
        )
        batch_op.create_index(
            "ix_webhook_deliveries_status", ["status"], unique=False
        )


def downgrade():
    with op.batch_alter_table("webhook_deliveries", schema=None) as batch_op:
        batch_op.drop_index("ix_webhook_deliveries_status")
        batch_op.drop_index("ix_webhook_deliveries_event_type")
        batch_op.drop_index("ix_webhook_deliveries_organization_id")
        batch_op.drop_index("ix_webhook_deliveries_endpoint_id")

    op.drop_table("webhook_deliveries")

    with op.batch_alter_table("webhook_endpoints", schema=None) as batch_op:
        batch_op.drop_index("ix_webhook_endpoints_provider")
        batch_op.drop_index("ix_webhook_endpoints_organization_id")

    op.drop_table("webhook_endpoints")

