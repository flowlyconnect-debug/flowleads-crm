"""External CRM connections (HubSpot, Pipedrive) and exported lead links.

Revision ID: f3c4d5e6f7a8
Revises: f2b3c4d5e6f7
"""

import sqlalchemy as sa
from alembic import op

revision = "f3c4d5e6f7a8"
down_revision = "f2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("account_label", sa.String(length=255), nullable=True),
        sa.Column("connected_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("organization_id", "provider", name="uq_crm_connection_org_provider"),
    )
    op.create_index("ix_crm_connections_organization_id", "crm_connections", ["organization_id"])
    op.create_table(
        "crm_export_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("company_id", sa.String(length=64), nullable=True),
        sa.Column("contact_id", sa.String(length=64), nullable=True),
        sa.Column("deal_id", sa.String(length=64), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("lead_id", "provider", name="uq_crm_export_link_lead_provider"),
    )
    op.create_index("ix_crm_export_links_organization_id", "crm_export_links", ["organization_id"])
    op.create_index("ix_crm_export_links_lead_id", "crm_export_links", ["lead_id"])


def downgrade():
    op.drop_index("ix_crm_export_links_lead_id", table_name="crm_export_links")
    op.drop_index("ix_crm_export_links_organization_id", table_name="crm_export_links")
    op.drop_table("crm_export_links")
    op.drop_index("ix_crm_connections_organization_id", table_name="crm_connections")
    op.drop_table("crm_connections")
