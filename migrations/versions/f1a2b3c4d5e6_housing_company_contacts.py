"""Board chair and maintenance company on listings; property management offices.

Revision ID: f1a2b3c4d5e6
Revises: fa0b1c2d3e4f
"""

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "fa0b1c2d3e4f"
branch_labels = None
depends_on = None

_LISTING_COLUMNS = (
    ("hallitus_pj", 255),
    ("hallitus_pj_puh", 50),
    ("hallitus_pj_email", 255),
    ("huoltoyhtio", 255),
    ("huoltoyhtio_puh", 50),
    ("huoltoyhtio_email", 255),
)


def upgrade():
    with op.batch_alter_table("listings") as batch:
        for name, length in _LISTING_COLUMNS:
            batch.add_column(sa.Column(name, sa.String(length=length), nullable=True))
    op.create_table(
        "property_manager_offices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name_key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ytunnus", sa.String(length=20), nullable=True),
        sa.Column("osoite", sa.String(length=255), nullable=True),
        sa.Column("postinumero", sa.String(length=10), nullable=True),
        sa.Column("kaupunki", sa.String(length=100), nullable=True),
        sa.Column("puhelin", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name_key", name="uq_property_manager_offices_name_key"),
    )


def downgrade():
    op.drop_table("property_manager_offices")
    with op.batch_alter_table("listings") as batch:
        for name, _length in reversed(_LISTING_COLUMNS):
            batch.drop_column(name)
