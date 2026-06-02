"""Add companies and contacts module

Revision ID: m8n7b6v5c4d3
Revises: r3s4t5u6v7w8
Create Date: 2026-06-02 09:10:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "m8n7b6v5c4d3"
down_revision = "r3s4t5u6v7w8"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=100), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True, server_default="Finland"),
        sa.Column("revenue_range", sa.String(length=50), nullable=True),
        sa.Column("employee_count", sa.String(length=50), nullable=True),
        sa.Column("type", sa.String(length=50), nullable=True, server_default="prospect"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tags", json_type, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.create_index(
            "ix_companies_organization_id", ["organization_id"], unique=False
        )
        batch_op.create_index("ix_companies_name", ["name"], unique=False)

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=100), nullable=True),
        sa.Column("linkedin_url", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tags", json_type, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("contacts", schema=None) as batch_op:
        batch_op.create_index(
            "ix_contacts_organization_id", ["organization_id"], unique=False
        )
        batch_op.create_index("ix_contacts_company_id", ["company_id"], unique=False)
        batch_op.create_index("ix_contacts_email", ["email"], unique=False)

    op.create_table(
        "lead_contacts",
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("lead_id", "contact_id"),
    )

    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.add_column(sa.Column("company_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_leads_company_id"), ["company_id"], unique=False
        )


def downgrade():
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_leads_company_id"))
        batch_op.drop_column("company_id")

    op.drop_table("lead_contacts")
    op.drop_table("contacts")
    op.drop_table("companies")

