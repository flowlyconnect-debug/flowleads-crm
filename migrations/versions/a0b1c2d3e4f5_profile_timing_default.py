"""Kohteet shows what fits the profile: a profile without a timing window gets
the default one (renovations starting this year or in the next two, "3 vuotta";
app/search/constants.py::DEFAULT_TIMING_YEARS) and is rematched, so a decided
renovation years away no longer fills the list. The customer can clear it.

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
"""

from alembic import op

revision = "a0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE search_profiles SET timing_within_years = 2, needs_rematch = TRUE "
        "WHERE timing_within_years IS NULL"
    )


def downgrade():
    # which profiles had no window before is not known: they keep the default
    pass
