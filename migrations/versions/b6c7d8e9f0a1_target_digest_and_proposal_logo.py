"""Daily target digest setting and proposal logo.

* organization_settings.targets_digest_enabled / targets_digest_sent_at: the
  morning summary of new targets (Kohteet page), on by default.
* proposal_templates.logo_url: company logo at the top of the proposal PDF.

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
"""

import sqlalchemy as sa
from alembic import op

revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organization_settings",
        sa.Column("targets_digest_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "organization_settings",
        sa.Column("targets_digest_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("proposal_templates", sa.Column("logo_url", sa.String(length=500), nullable=True))


def downgrade():
    with op.batch_alter_table("proposal_templates") as batch_op:
        batch_op.drop_column("logo_url")
    with op.batch_alter_table("organization_settings") as batch_op:
        batch_op.drop_column("targets_digest_sent_at")
        batch_op.drop_column("targets_digest_enabled")
