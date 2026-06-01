"""Ensure leads industry and region columns exist

Revision ID: r3s4t5u6v7w8
Revises: q2r3s4t5u6v7
Create Date: 2026-06-01 23:15:00.000000

Idempotent fix for production DBs stamped at q2r3s4t5u6v7 but missing
leads.industry / leads.region (partial or skipped column adds).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "r3s4t5u6v7w8"
down_revision = "q2r3s4t5u6v7"
branch_labels = None
depends_on = None


def _lead_column_names() -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns("leads")}


def upgrade():
    columns = _lead_column_names()
    if "industry" not in columns:
        op.add_column("leads", sa.Column("industry", sa.String(length=100), nullable=True))
    if "region" not in columns:
        op.add_column("leads", sa.Column("region", sa.String(length=100), nullable=True))


def downgrade():
    columns = _lead_column_names()
    if "region" in columns:
        op.drop_column("leads", "region")
    if "industry" in columns:
        op.drop_column("leads", "industry")
