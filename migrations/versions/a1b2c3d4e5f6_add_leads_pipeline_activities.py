"""Add leads pipeline and activities tables

Revision ID: a1b2c3d4e5f6
Revises: d19cff71315e
Create Date: 2026-05-25 22:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "d19cff71315e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pipeline_stages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_pipeline_stage_org_name"),
        sa.UniqueConstraint("organization_id", "order_index", name="uq_pipeline_stage_org_order"),
    )
    with op.batch_alter_table("pipeline_stages", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_pipeline_stages_organization_id"), ["organization_id"], unique=False)

    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("assigned_to", sa.Integer(), nullable=True),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=150), nullable=True),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
        sa.Column("stage_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("ai_enriched", sa.Boolean(), nullable=False),
        sa.Column("ai_enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("ai_company_info", sa.JSON(), nullable=True),
        sa.Column("ai_contact_info", sa.JSON(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("score_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["stage_id"], ["pipeline_stages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "source", "source_ref", name="uq_lead_org_source_ref"),
    )
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_leads_assigned_to"), ["assigned_to"], unique=False)
        batch_op.create_index(batch_op.f("ix_leads_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_leads_organization_id"), ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_leads_stage_id"), ["stage_id"], unique=False)
        batch_op.create_index("ix_leads_source_source_ref", ["source", "source_ref"], unique=False)

    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("activities", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_activities_lead_id"), ["lead_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_activities_organization_id"), ["organization_id"], unique=False)


def downgrade():
    with op.batch_alter_table("activities", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_activities_organization_id"))
        batch_op.drop_index(batch_op.f("ix_activities_lead_id"))
    op.drop_table("activities")
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.drop_index("ix_leads_source_source_ref")
        batch_op.drop_index(batch_op.f("ix_leads_stage_id"))
        batch_op.drop_index(batch_op.f("ix_leads_organization_id"))
        batch_op.drop_index(batch_op.f("ix_leads_created_at"))
        batch_op.drop_index(batch_op.f("ix_leads_assigned_to"))
    op.drop_table("leads")
    with op.batch_alter_table("pipeline_stages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pipeline_stages_organization_id"))
    op.drop_table("pipeline_stages")
