"""listing_renovations.stage: decided / planned / need (app/listings/stages.py).
Existing rows stay NULL and are read on the fly; new rows get it on save.

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
"""

import sqlalchemy as sa
from alembic import op

revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("listing_renovations") as batch:
        batch.add_column(sa.Column("stage", sa.String(10), nullable=True))


def downgrade():
    with op.batch_alter_table("listing_renovations") as batch:
        batch.drop_column("stage")
