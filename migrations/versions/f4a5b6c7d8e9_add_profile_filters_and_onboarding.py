"""Profile filters (cities, several renovation types, timing, building),
listing build year / building type, forced password change, feature backfill.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

ALL_FEATURES = ["sequences", "automations", "ai_enrichment"]


def upgrade():
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    op.add_column("search_profiles", sa.Column("remonttityypit", json_type, nullable=True))
    op.add_column("search_profiles", sa.Column("cities", json_type, nullable=True))
    op.add_column("search_profiles", sa.Column("timing_within_years", sa.Integer(), nullable=True))
    op.add_column("search_profiles", sa.Column("build_year_min", sa.Integer(), nullable=True))
    op.add_column("search_profiles", sa.Column("build_year_max", sa.Integer(), nullable=True))
    op.add_column("search_profiles", sa.Column("building_types", json_type, nullable=True))

    op.add_column("listings", sa.Column("build_year", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("building_type", sa.String(length=30), nullable=True))
    op.add_column("listing_renovations", sa.Column("timing_year", sa.Integer(), nullable=True))

    # Feature gating starts being enforced with this release. Organizations
    # that never chose features (everything created before gating, i.e. an
    # empty list) keep full access; a superadmin can narrow it afterwards.
    bind = op.get_bind()
    organizations = sa.table(
        "organizations", sa.column("id", sa.Integer), sa.column("enabled_features", sa.JSON)
    )
    rows = bind.execute(sa.select(organizations.c.id, organizations.c.enabled_features)).fetchall()
    for org_id, features in rows:
        if isinstance(features, str):
            try:
                features = json.loads(features)
            except ValueError:
                features = []
        if not features:
            bind.execute(
                organizations.update().where(organizations.c.id == org_id).values(enabled_features=ALL_FEATURES)
            )


def downgrade():
    with op.batch_alter_table("listing_renovations") as batch:
        batch.drop_column("timing_year")
    with op.batch_alter_table("listings") as batch:
        batch.drop_column("building_type")
        batch.drop_column("build_year")
    with op.batch_alter_table("search_profiles") as batch:
        batch.drop_column("building_types")
        batch.drop_column("build_year_max")
        batch.drop_column("build_year_min")
        batch.drop_column("timing_within_years")
        batch.drop_column("cities")
        batch.drop_column("remonttityypit")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("must_change_password")
