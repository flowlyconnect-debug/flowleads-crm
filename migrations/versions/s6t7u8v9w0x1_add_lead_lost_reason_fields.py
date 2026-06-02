"""Add lead lost reason fields

Revision ID: s6t7u8v9w0x1
Revises: r3s4t5u6v7w8
Create Date: 2026-06-02 10:20:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "s6t7u8v9w0x1"
down_revision = "r3s4t5u6v7w8"
branch_labels = None
depends_on = None


def _lead_column_names() -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns("leads")}


def upgrade():
    columns = _lead_column_names()
    if "lost_reason" not in columns:
        op.add_column("leads", sa.Column("lost_reason", sa.String(length=100), nullable=True))
    if "lost_reason_note" not in columns:
        op.add_column("leads", sa.Column("lost_reason_note", sa.Text(), nullable=True))


def downgrade():
    columns = _lead_column_names()
    if "lost_reason_note" in columns:
        op.drop_column("leads", "lost_reason_note")
    if "lost_reason" in columns:
        op.drop_column("leads", "lost_reason")
