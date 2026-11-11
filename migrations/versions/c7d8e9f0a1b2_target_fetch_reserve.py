"""Target reserve for the "Hae kohteita" button.

* listing_matches.released_at: when a match became visible on Kohteet (NULL =
  in the reserve). Every existing match is marked released at its creation
  time, so nothing a customer already sees disappears.
* organization_settings.targets_fetched_at: last press of the button.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
"""

import sqlalchemy as sa
from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("listing_matches", sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE listing_matches SET released_at = created_at")
    op.add_column(
        "organization_settings",
        sa.Column("targets_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    with op.batch_alter_table("organization_settings") as batch_op:
        batch_op.drop_column("targets_fetched_at")
    with op.batch_alter_table("listing_matches") as batch_op:
        batch_op.drop_column("released_at")
