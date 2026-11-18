"""Curated Kohteet: listing_matches.tier (A/B/C) and the per-customer visible
limit and lowest class (organizations.targets_visible_limit, targets_min_tier).
Matches already visible stay visible.

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
"""

import sqlalchemy as sa
from alembic import op

revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("listing_matches") as batch:
        batch.add_column(sa.Column("tier", sa.String(1), nullable=True))
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("targets_visible_limit", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("targets_min_tier", sa.String(1), nullable=True))
    op.create_index("ix_listing_matches_release", "listing_matches", ["organization_id", "status", "released_at"])
    # released_at NULL now means "waiting": nothing a customer already sees may vanish
    op.execute("UPDATE listing_matches SET released_at = created_at WHERE released_at IS NULL")


def downgrade():
    op.drop_index("ix_listing_matches_release", table_name="listing_matches")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("targets_min_tier")
        batch.drop_column("targets_visible_limit")
    with op.batch_alter_table("listing_matches") as batch:
        batch.drop_column("tier")
