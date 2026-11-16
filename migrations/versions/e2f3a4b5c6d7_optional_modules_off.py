"""Optional modules off for every organization.

Sequences, automations and AI enrichment are hidden from customers until each
is ready for them; a superadmin switches a module back on per customer on
/admin/customers ("Toiminnot käytössä").

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""

from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE organizations SET enabled_features = '[]'")


def downgrade():
    pass  # data-only: which modules each organization had is not kept
