"""Map position of listings.

Revision ID: f2b3c4d5e6f7
Revises: fb1c2d3e4f5a
"""

import sqlalchemy as sa
from alembic import op

revision = "f2b3c4d5e6f7"
down_revision = "fb1c2d3e4f5a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("listings") as batch:
        batch.add_column(sa.Column("lat", sa.Float(), nullable=True))
        batch.add_column(sa.Column("lon", sa.Float(), nullable=True))
        batch.add_column(sa.Column("geo_precision", sa.String(length=10), nullable=True))
        batch.add_column(sa.Column("geocoded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("listings") as batch:
        batch.drop_column("geocoded_at")
        batch.drop_column("geo_precision")
        batch.drop_column("lon")
        batch.drop_column("lat")
