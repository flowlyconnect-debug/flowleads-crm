"""Shared Oikotie listing catalogue + profile matching fields

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-11-11 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="oikotie"),
        sa.Column("external_id", sa.String(50), nullable=False),
        sa.Column("url", sa.String(500)),
        sa.Column("maakunta", sa.String(50), nullable=False),
        sa.Column("kaupunki", sa.String(100)),
        sa.Column("osoite", sa.String(255)),
        sa.Column("hinta", sa.String(50)),
        sa.Column("alue", sa.String(50)),
        sa.Column("description", sa.Text()),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column("analysis_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("analysis_claimed_at", sa.DateTime(timezone=True)),
        sa.Column("analysis_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analyzed_at", sa.DateTime(timezone=True)),
        sa.Column("analysis_error", sa.Text()),
        sa.Column("taloyhtio", sa.String(255)),
        sa.Column("tulevat_remontit", sa.Text()),
        sa.Column("isannoitsija", sa.String(255)),
        sa.Column("isannoitsija_puh", sa.String(50)),
        sa.Column("isannoitsija_email", sa.String(255)),
        sa.Column("isannoitsija_website", sa.String(500)),
        sa.Column("contact_source", sa.String(50)),
        sa.Column("contact_status", sa.String(20), nullable=False, server_default="none"),
        sa.Column("contact_claimed_at", sa.DateTime(timezone=True)),
        sa.Column("contact_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source", "external_id", name="uq_listing_source_external_id"),
    )
    op.create_index("ix_listings_analysis_queue", "listings", ["analysis_status", "is_active"])
    op.create_index("ix_listings_contact_queue", "listings", ["contact_status", "is_active"])
    op.create_index("ix_listings_maakunta_active", "listings", ["maakunta", "is_active"])

    op.create_table(
        "listing_renovations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("remonttityyppi", sa.String(100), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timing", sa.String(100)),
        sa.Column("evidence", sa.Text()),
    )
    op.create_index("ix_listing_renovations_listing_id", "listing_renovations", ["listing_id"])
    op.create_index("ix_listing_renovations_type_conf", "listing_renovations", ["remonttityyppi", "confidence"])

    op.create_table(
        "listing_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("search_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("leads.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lead_created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("profile_id", "listing_id", name="uq_listing_match_profile_listing"),
    )
    op.create_index("ix_listing_matches_organization_id", "listing_matches", ["organization_id"])
    op.create_index("ix_listing_matches_profile_id", "listing_matches", ["profile_id"])
    op.create_index("ix_listing_matches_listing_id", "listing_matches", ["listing_id"])
    op.create_index("ix_listing_matches_status", "listing_matches", ["status", "profile_id"])

    op.create_table(
        "listing_crawl_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(30), nullable=False, server_default="oikotie"),
        sa.Column("maakunta", sa.String(50), nullable=False),
        sa.Column("full_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("full_started_at", sa.DateTime(timezone=True)),
        sa.Column("prev_full_started_at", sa.DateTime(timezone=True)),
        sa.Column("full_completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_incremental_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source", "maakunta", name="uq_crawl_state_source_maakunta"),
    )

    op.add_column("search_profiles", sa.Column("min_confidence", sa.Integer(), nullable=False, server_default="60"))
    op.add_column("search_profiles", sa.Column("daily_lead_limit", sa.Integer(), nullable=False, server_default="50"))
    op.add_column(
        "search_profiles",
        sa.Column("needs_rematch", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade():
    with op.batch_alter_table("search_profiles", schema=None) as batch_op:
        batch_op.drop_column("needs_rematch")
        batch_op.drop_column("daily_lead_limit")
        batch_op.drop_column("min_confidence")
    op.drop_table("listing_crawl_state")
    op.drop_table("listing_matches")
    op.drop_table("listing_renovations")
    op.drop_table("listings")
