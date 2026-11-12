"""Housing-company keys on listings, target score and hide time on matches.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
"""

import sqlalchemy as sa
from alembic import op

revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("listings") as batch:
        batch.add_column(sa.Column("company_key", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("company_name_key", sa.String(length=300), nullable=True))
        batch.create_index("ix_listings_company_key", ["company_key"])
        batch.create_index("ix_listings_company_name_key", ["company_name_key"])
    with op.batch_alter_table("listing_matches") as batch:
        batch.add_column(sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("score", sa.Integer(), nullable=True))
    # Existing listings get their keys from the scheduler
    # (app/listings/housing.py::backfill_company_keys, NULL name key = not done yet).


def downgrade():
    with op.batch_alter_table("listing_matches") as batch:
        batch.drop_column("score")
        batch.drop_column("dismissed_at")
    with op.batch_alter_table("listings") as batch:
        batch.drop_index("ix_listings_company_name_key")
        batch.drop_index("ix_listings_company_key")
        batch.drop_column("company_name_key")
        batch.drop_column("company_key")
