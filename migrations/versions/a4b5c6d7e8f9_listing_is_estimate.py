"""Listing.is_estimate: targets estimated from the public registers
(app/registry/publish.py) are marked "Ennuste".

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
"""

import sqlalchemy as sa
from alembic import op

revision = "a4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("listings") as batch:
        batch.add_column(sa.Column("is_estimate", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("listings") as batch:
        batch.drop_column("is_estimate")
