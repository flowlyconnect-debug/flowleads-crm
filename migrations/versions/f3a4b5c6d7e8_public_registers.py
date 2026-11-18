"""Public registers: Ryhti buildings, permits and addresses, PRH housing
companies and the renovation signals derived from them (app/registry/).
Internal only: nothing here is shown to customers.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
"""

import sqlalchemy as sa
from alembic import op

revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "registry_housing_companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(12), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("company_key", sa.String(300), nullable=True),
        sa.Column("municipality", sa.String(100), nullable=True),
        sa.Column("street", sa.String(255), nullable=True),
        sa.Column("postal_code", sa.String(10), nullable=True),
        sa.Column("post_office", sa.String(100), nullable=True),
        sa.Column("registration_date", sa.Date(), nullable=True),
        sa.Column("prh_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_registry_companies_municipality", "registry_housing_companies", ["municipality"])
    op.create_index("ix_registry_companies_company_key", "registry_housing_companies", ["company_key"])

    op.create_table(
        "registry_buildings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("building_key", sa.String(40), nullable=False, unique=True),
        sa.Column("building_id", sa.String(20), nullable=True),
        sa.Column("property_id", sa.String(20), nullable=True),
        sa.Column("municipality", sa.String(100), nullable=True),
        sa.Column("building_type", sa.String(30), nullable=False),
        sa.Column("completion_year", sa.Integer(), nullable=True),
        sa.Column("apartments", sa.Integer(), nullable=True),
        sa.Column("floors", sa.Integer(), nullable=True),
        sa.Column("floor_area", sa.Integer(), nullable=True),
        sa.Column("facade_material", sa.String(50), nullable=True),
        sa.Column("construction_method", sa.String(50), nullable=True),
        sa.Column("heating_method", sa.String(50), nullable=True),
        sa.Column("heating_source", sa.String(50), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("postal_code", sa.String(10), nullable=True),
        sa.Column("post_office", sa.String(100), nullable=True),
        sa.Column("address_keys", sa.JSON(), nullable=True),
        sa.Column("addresses_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "housing_company_id",
            sa.Integer(),
            sa.ForeignKey("registry_housing_companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("link_method", sa.String(20), nullable=True),
        sa.Column("ryhti_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_registry_buildings_municipality", "registry_buildings", ["municipality"])
    op.create_index("ix_registry_buildings_company", "registry_buildings", ["housing_company_id"])
    op.create_index("ix_registry_buildings_address_pending", "registry_buildings", ["addresses_fetched_at"])
    op.create_index("ix_registry_buildings_building_id", "registry_buildings", ["building_id"])

    op.create_table(
        "registry_permits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("permit_id", sa.String(30), nullable=False),
        sa.Column("building_key", sa.String(40), nullable=False),
        sa.Column("building_id", sa.String(20), nullable=True),
        sa.Column("municipality", sa.String(100), nullable=True),
        sa.Column("building_type", sa.String(30), nullable=True),
        sa.Column("action_type", sa.String(80), nullable=True),
        sa.Column("action_state", sa.String(30), nullable=True),
        sa.Column("decision_date", sa.Date(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("completion_date", sa.Date(), nullable=True),
        sa.Column("ryhti_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("permit_id", "building_key", name="uq_registry_permit_building"),
    )
    op.create_index("ix_registry_permits_building_id", "registry_permits", ["building_id"])

    op.create_table(
        "registry_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "building_id",
            sa.Integer(),
            sa.ForeignKey("registry_buildings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("renovation_type", sa.String(80), nullable=False),
        sa.Column("due_year", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("building_id", "kind", "renovation_type", name="uq_registry_signal"),
    )
    op.create_index("ix_registry_signals_kind", "registry_signals", ["kind"])

    op.create_table(
        "registry_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("cursor", sa.String(40), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_table("registry_sync_state")
    op.drop_index("ix_registry_signals_kind", table_name="registry_signals")
    op.drop_table("registry_signals")
    op.drop_index("ix_registry_permits_building_id", table_name="registry_permits")
    op.drop_table("registry_permits")
    op.drop_index("ix_registry_buildings_building_id", table_name="registry_buildings")
    op.drop_index("ix_registry_buildings_address_pending", table_name="registry_buildings")
    op.drop_index("ix_registry_buildings_company", table_name="registry_buildings")
    op.drop_index("ix_registry_buildings_municipality", table_name="registry_buildings")
    op.drop_table("registry_buildings")
    op.drop_index("ix_registry_companies_company_key", table_name="registry_housing_companies")
    op.drop_index("ix_registry_companies_municipality", table_name="registry_housing_companies")
    op.drop_table("registry_housing_companies")
