"""Add custom fields and segments

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-26 20:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "g7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    op.create_table(
        "custom_field_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("field_type", sa.String(length=32), nullable=False),
        sa.Column("options", json_type, nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_searchable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "entity_type",
            "name",
            name="uq_custom_field_def_org_entity_name",
        ),
    )
    with op.batch_alter_table("custom_field_definitions", schema=None) as batch_op:
        batch_op.create_index(
            "ix_custom_field_definitions_organization_id",
            ["organization_id"],
            unique=False,
        )

    op.create_table(
        "custom_field_values",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("field_definition_id", sa.Integer(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_number", sa.Float(), nullable=True),
        sa.Column("value_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("value_boolean", sa.Boolean(), nullable=True),
        sa.Column("value_json", json_type, nullable=True),
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
        sa.ForeignKeyConstraint(
            ["field_definition_id"], ["custom_field_definitions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "entity_type",
            "entity_id",
            "field_definition_id",
            name="uq_custom_field_value_entity_field",
        ),
    )
    with op.batch_alter_table("custom_field_values", schema=None) as batch_op:
        batch_op.create_index(
            "ix_custom_field_values_organization_id", ["organization_id"], unique=False
        )
        batch_op.create_index(
            "ix_custom_field_values_field_definition_id",
            ["field_definition_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_custom_field_values_entity_id", ["entity_id"], unique=False
        )

    op.create_table(
        "segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("filters", json_type, nullable=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lead_count_cache", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_segment_org_name"),
    )
    with op.batch_alter_table("segments", schema=None) as batch_op:
        batch_op.create_index(
            "ix_segments_organization_id", ["organization_id"], unique=False
        )


def downgrade():
    op.drop_table("segments")
    op.drop_table("custom_field_values")
    op.drop_table("custom_field_definitions")
