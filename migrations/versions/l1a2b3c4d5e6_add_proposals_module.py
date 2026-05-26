"""Add proposals module

Revision ID: l1a2b3c4d5e6
Revises: k0f1a2b3c4d5
Create Date: 2026-05-26 26:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "l1a2b3c4d5e6"
down_revision = "k0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organization_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("proposal_sequence_json", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "proposal_move_lead_to_won_on_accept",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "proposal_default_valid_days",
                sa.Integer(),
                nullable=False,
                server_default="30",
            )
        )
        batch_op.add_column(
            sa.Column(
                "proposal_default_tax_percent",
                sa.Numeric(5, 2),
                nullable=False,
                server_default="24",
            )
        )
        batch_op.add_column(sa.Column("proposal_default_notes", sa.Text(), nullable=True))

    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("reference_number", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="EUR"),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("tax_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_token", sa.String(length=64), nullable=True),
        sa.Column("signature_name", sa.String(length=255), nullable=True),
        sa.Column("signature_ip", sa.String(length=64), nullable=True),
        sa.Column("signature_user_agent", sa.String(length=500), nullable=True),
        sa.Column("opened_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lead_name_snapshot", sa.String(length=255), nullable=True),
        sa.Column("lead_company_snapshot", sa.String(length=255), nullable=True),
        sa.Column("lead_email_snapshot", sa.String(length=255), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("view_token"),
    )
    with op.batch_alter_table("proposals", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_proposals_lead_id"), ["lead_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_proposals_organization_id"), ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_proposals_reference_number"), ["reference_number"], unique=False)
        batch_op.create_index(batch_op.f("ix_proposals_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_proposals_view_token"), ["view_token"], unique=False)

    op.create_table(
        "proposal_line_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("proposal_line_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_proposal_line_items_proposal_id"), ["proposal_id"], unique=False)

    op.create_table(
        "proposal_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("default_valid_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("default_notes", sa.Text(), nullable=True),
        sa.Column("default_tax_percent", sa.Numeric(5, 2), nullable=False, server_default="24"),
        sa.Column("header_html", sa.Text(), nullable=True),
        sa.Column("footer_html", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("proposal_templates", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_proposal_templates_organization_id"), ["organization_id"], unique=False
        )


def downgrade():
    op.drop_table("proposal_templates")
    op.drop_table("proposal_line_items")
    op.drop_table("proposals")
    with op.batch_alter_table("organization_settings", schema=None) as batch_op:
        batch_op.drop_column("proposal_default_notes")
        batch_op.drop_column("proposal_default_tax_percent")
        batch_op.drop_column("proposal_default_valid_days")
        batch_op.drop_column("proposal_move_lead_to_won_on_accept")
        batch_op.drop_column("proposal_sequence_json")
