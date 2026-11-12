"""Monthly results report switch.

Revision ID: f4d5e6f7a8b9
Revises: f3c4d5e6f7a8
"""

import sqlalchemy as sa
from alembic import op

revision = "f4d5e6f7a8b9"
down_revision = "f3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organization_settings") as batch:
        batch.add_column(sa.Column("monthly_report_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("monthly_report_sent_for", sa.String(length=7), nullable=True))


def downgrade():
    with op.batch_alter_table("organization_settings") as batch:
        batch.drop_column("monthly_report_sent_for")
        batch.drop_column("monthly_report_enabled")
