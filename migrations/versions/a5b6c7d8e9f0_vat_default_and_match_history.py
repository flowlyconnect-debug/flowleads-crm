"""Default VAT 24 -> 25.5 %, and listing matches that outlive their profile.

* The Finnish general VAT rate has been 25.5 % since 1.9.2024. Organizations
  and proposal templates that still carry the old default of 24 are moved to
  25.5; a rate someone chose on purpose (anything else) is left alone, and so
  are existing proposals.
* listing_matches.profile_id becomes nullable: deleting a search profile keeps
  the matches the customer already handled (moved to leads, merged, hidden),
  so a new profile does not offer the same housing companies again.

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
"""

import sqlalchemy as sa
from alembic import op

revision = "a5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE organization_settings SET proposal_default_tax_percent = 25.5 "
        "WHERE proposal_default_tax_percent = 24"
    )
    op.execute(
        "UPDATE proposal_templates SET default_tax_percent = 25.5 WHERE default_tax_percent = 24"
    )
    with op.batch_alter_table("organization_settings") as batch_op:
        batch_op.alter_column(
            "proposal_default_tax_percent",
            existing_type=sa.Numeric(5, 2),
            existing_nullable=False,
            server_default="25.5",
        )
    with op.batch_alter_table("proposal_templates") as batch_op:
        batch_op.alter_column(
            "default_tax_percent",
            existing_type=sa.Numeric(5, 2),
            existing_nullable=False,
            server_default="25.5",
        )
    with op.batch_alter_table("listing_matches") as batch_op:
        batch_op.alter_column("profile_id", existing_type=sa.Integer(), nullable=True)


def downgrade():
    # matches without a profile cannot exist under the old schema
    op.execute("DELETE FROM listing_matches WHERE profile_id IS NULL")
    with op.batch_alter_table("listing_matches") as batch_op:
        batch_op.alter_column("profile_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("proposal_templates") as batch_op:
        batch_op.alter_column(
            "default_tax_percent",
            existing_type=sa.Numeric(5, 2),
            existing_nullable=False,
            server_default="24",
        )
    with op.batch_alter_table("organization_settings") as batch_op:
        batch_op.alter_column(
            "proposal_default_tax_percent",
            existing_type=sa.Numeric(5, 2),
            existing_nullable=False,
            server_default="24",
        )
