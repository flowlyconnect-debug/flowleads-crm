"""Listing contact person and housing company business ID; profile default
confidence 50; customer invites with several renovation types.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
"""

import sqlalchemy as sa
from alembic import op

revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("listings") as batch:
        batch.add_column(sa.Column("isannoitsija_henkilo", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("ytunnus", sa.String(length=20), nullable=True))
    # Imported research rows start at 50 % confidence; the old default 60 hid
    # every sixth of them. Profiles still on the old default move to 50 and are
    # rematched by the scheduler.
    with op.batch_alter_table("search_profiles") as batch:
        batch.alter_column("min_confidence", server_default="50")
    # an invite can carry several renovation types ("A; B; C")
    with op.batch_alter_table("customer_invites") as batch:
        batch.alter_column("remonttityyppi", type_=sa.String(length=500), existing_nullable=False)
    op.execute(
        "UPDATE search_profiles SET min_confidence = 50, needs_rematch = true WHERE min_confidence = 60"
    )


def downgrade():
    with op.batch_alter_table("customer_invites") as batch:
        batch.alter_column("remonttityyppi", type_=sa.String(length=100), existing_nullable=False)
    with op.batch_alter_table("search_profiles") as batch:
        batch.alter_column("min_confidence", server_default="60")
    with op.batch_alter_table("listings") as batch:
        batch.drop_column("ytunnus")
        batch.drop_column("isannoitsija_henkilo")
