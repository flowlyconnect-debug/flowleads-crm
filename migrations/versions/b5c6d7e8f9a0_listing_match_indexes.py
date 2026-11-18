"""Indexes for the Kohteet queries: listing_matches (organization_id, status)
and listing_matches.lead_id (a foreign key with ON DELETE SET NULL).

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
"""

from alembic import op

revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_listing_matches_org_status", "listing_matches", ["organization_id", "status"])
    op.create_index("ix_listing_matches_lead_id", "listing_matches", ["lead_id"])


def downgrade():
    op.drop_index("ix_listing_matches_lead_id", table_name="listing_matches")
    op.drop_index("ix_listing_matches_org_status", table_name="listing_matches")
