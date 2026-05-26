"""Add predictive analytics (deal probability, prediction logs)

Revision ID: m1n2o3p4q5r6
Revises: l1a2b3c4d5e6
Create Date: 2026-05-26 28:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "m1n2o3p4q5r6"
down_revision = "l1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.add_column(sa.Column("close_probability", sa.Numeric(5, 4), nullable=True))
        batch_op.add_column(sa.Column("probability_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("expected_value", sa.Numeric(14, 2), nullable=True))
        batch_op.add_column(sa.Column("deal_value", sa.Numeric(14, 2), nullable=True))

    with op.batch_alter_table("organization_settings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("default_currency", sa.String(3), nullable=False, server_default="EUR")
        )

    op.create_table(
        "prediction_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("probability", sa.Numeric(5, 4), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=True),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("key_positive_signals", sa.JSON(), nullable=True),
        sa.Column("key_risk_signals", sa.JSON(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_prediction_logs_lead_id"), "prediction_logs", ["lead_id"], unique=False
    )
    op.create_index(
        op.f("ix_prediction_logs_organization_id"),
        "prediction_logs",
        ["organization_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_prediction_logs_organization_id"), table_name="prediction_logs")
    op.drop_index(op.f("ix_prediction_logs_lead_id"), table_name="prediction_logs")
    op.drop_table("prediction_logs")

    with op.batch_alter_table("organization_settings", schema=None) as batch_op:
        batch_op.drop_column("default_currency")

    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.drop_column("deal_value")
        batch_op.drop_column("expected_value")
        batch_op.drop_column("probability_updated_at")
        batch_op.drop_column("close_probability")
