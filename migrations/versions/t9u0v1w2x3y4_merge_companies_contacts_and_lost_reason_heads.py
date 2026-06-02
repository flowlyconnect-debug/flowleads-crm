"""Merge companies/contacts and lost-reason migration heads.

Revision ID: t9u0v1w2x3y4
Revises: m8n7b6v5c4d3, s6t7u8v9w0x1
Create Date: 2026-06-02 11:36:00.000000
"""

from alembic import op

revision = "t9u0v1w2x3y4"
down_revision = ("m8n7b6v5c4d3", "s6t7u8v9w0x1")
branch_labels = None
depends_on = None


def upgrade():
    # Merge revision: no schema changes.
    pass


def downgrade():
    # Merge revision: no schema changes.
    pass

