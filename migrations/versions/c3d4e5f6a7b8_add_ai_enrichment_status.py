"""Add AI enrichment status columns to leads

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-26 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "ai_enrichment_status",
                sa.String(length=32),
                nullable=False,
                server_default="disabled",
            )
        )
        batch_op.add_column(
            sa.Column("ai_enrichment_error", sa.Text(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.drop_column("ai_enrichment_error")
        batch_op.drop_column("ai_enrichment_status")
