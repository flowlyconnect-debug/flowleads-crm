"""Add stage_changed_at to leads

Revision ID: u3v4w5x6y7z8
Revises: t9u0v1w2x3y4
Create Date: 2026-06-02 13:20:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "u3v4w5x6y7z8"
down_revision = "t9u0v1w2x3y4"
branch_labels = None
depends_on = None


def _lead_column_names() -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns("leads")}


def upgrade():
    columns = _lead_column_names()
    if "stage_changed_at" not in columns:
        op.add_column("leads", sa.Column("stage_changed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    columns = _lead_column_names()
    if "stage_changed_at" in columns:
        op.drop_column("leads", "stage_changed_at")
