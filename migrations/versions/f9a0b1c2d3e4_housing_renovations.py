"""The renovation outlook of each housing company, combined from every
source (housing_renovations, housing_companies.outlook_at; app/listings/forecast.py).

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
"""

import sqlalchemy as sa
from alembic import op

revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("housing_companies") as batch:
        batch.add_column(sa.Column("outlook_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "housing_renovations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("housing_id", sa.Integer(), sa.ForeignKey("housing_companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("remonttityyppi", sa.String(100), nullable=False),
        sa.Column("stage", sa.String(10), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("estimate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("housing_id", "remonttityyppi", name="uq_housing_renovation_type"),
    )
    op.create_index("ix_housing_renovations_housing_id", "housing_renovations", ["housing_id"])
    op.create_index("ix_housing_renovations_type_stage", "housing_renovations", ["remonttityyppi", "stage"])


def downgrade():
    op.drop_index("ix_housing_renovations_type_stage", table_name="housing_renovations")
    op.drop_index("ix_housing_renovations_housing_id", table_name="housing_renovations")
    op.drop_table("housing_renovations")
    with op.batch_alter_table("housing_companies") as batch:
        batch.drop_column("outlook_at")
