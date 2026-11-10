"""Add search_jobs.n8n_token_hash / n8n_token_expires_at

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-11-10 22:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "search_jobs",
        sa.Column("n8n_token_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "search_jobs",
        sa.Column("n8n_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("search_jobs", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_search_jobs_n8n_token_hash", ["n8n_token_hash"])


def downgrade():
    with op.batch_alter_table("search_jobs", schema=None) as batch_op:
        batch_op.drop_constraint("uq_search_jobs_n8n_token_hash", type_="unique")
    op.drop_column("search_jobs", "n8n_token_expires_at")
    op.drop_column("search_jobs", "n8n_token_hash")
