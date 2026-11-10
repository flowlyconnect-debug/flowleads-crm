"""Add search_jobs heartbeat/progress columns for page-by-page n8n runs

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-11-10 23:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("search_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "search_jobs",
        sa.Column("pages_done", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "search_jobs",
        sa.Column("pages_failed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("search_jobs", sa.Column("progress", sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("search_jobs", schema=None) as batch_op:
        batch_op.drop_column("progress")
        batch_op.drop_column("pages_failed")
        batch_op.drop_column("pages_done")
        batch_op.drop_column("heartbeat_at")
