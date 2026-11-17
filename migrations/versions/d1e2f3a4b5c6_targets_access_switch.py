"""Per-customer target access switch; release the old "Hae kohteita" reserve.

organizations.targets_enabled: a superadmin switches a customer's access to
the Kohteet page off (the count stays visible, the leads stay).

"Hae kohteita" is removed: every match is visible at once, so the matches
still waiting in the reserve (released_at IS NULL) are released now.
organization_settings.targets_fetched_at is no longer read; it stays in the
table because the previous release still selects it while this one starts.

Revision ID: d1e2f3a4b5c6
Revises: a1b2c3d4e5f7
"""

import sqlalchemy as sa
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organizations",
        sa.Column("targets_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute("UPDATE listing_matches SET released_at = CURRENT_TIMESTAMP WHERE released_at IS NULL")


def downgrade():
    # the released matches stay released: which ones were in the reserve is not kept
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("targets_enabled")
