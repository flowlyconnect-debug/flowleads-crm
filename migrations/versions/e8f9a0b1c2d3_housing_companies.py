"""One taloyhtiö across every source: housing_companies and their street
addresses, and the links from listings and the public registers to them
(app/listings/housing_registry.py).

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
"""

import sqlalchemy as sa
from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None

LINKED = ("listings", "registry_housing_companies", "registry_buildings")


def upgrade():
    op.create_table(
        "housing_companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.String(12), nullable=True, unique=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("name_key", sa.String(255), nullable=True),
        sa.Column("municipality", sa.String(100), nullable=True),
        sa.Column("municipality_key", sa.String(100), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("build_year", sa.Integer(), nullable=True),
        sa.Column("building_type", sa.String(30), nullable=True),
        sa.Column("apartments", sa.Integer(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_housing_companies_name", "housing_companies", ["name_key", "municipality_key"])
    op.create_index("ix_housing_companies_municipality", "housing_companies", ["municipality_key"])
    op.create_table(
        "housing_company_addresses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("housing_id", sa.Integer(), sa.ForeignKey("housing_companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("address_key", sa.String(120), nullable=False),
        sa.Column("municipality_key", sa.String(100), nullable=True),
        sa.UniqueConstraint("housing_id", "address_key", name="uq_housing_company_address"),
    )
    op.create_index("ix_housing_company_addresses_key", "housing_company_addresses", ["address_key", "municipality_key"])
    for table in LINKED:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("housing_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(f"fk_{table}_housing_id", "housing_companies", ["housing_id"], ["id"], ondelete="SET NULL")
            batch.create_index(f"ix_{table}_housing_id", ["housing_id"])


def downgrade():
    for table in LINKED:
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_housing_id")
            batch.drop_constraint(f"fk_{table}_housing_id", type_="foreignkey")
            batch.drop_column("housing_id")
    op.drop_index("ix_housing_company_addresses_key", table_name="housing_company_addresses")
    op.drop_table("housing_company_addresses")
    op.drop_index("ix_housing_companies_municipality", table_name="housing_companies")
    op.drop_index("ix_housing_companies_name", table_name="housing_companies")
    op.drop_table("housing_companies")
