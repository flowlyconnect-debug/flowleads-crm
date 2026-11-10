"""Add customer_invites table and organizations.enabled_features

Revision ID: z8a9b0c1d2e3
Revises: y7z8a9b0c1d2
Create Date: 2026-11-10 19:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "z8a9b0c1d2e3"
down_revision = "y7z8a9b0c1d2"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade():
    op.add_column(
        "organizations",
        sa.Column("enabled_features", json_type, nullable=False, server_default="[]"),
    )

    op.create_table(
        "customer_invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("allowed_features", json_type, nullable=False, server_default="[]"),
        sa.Column("search_profile_name", sa.String(length=100), nullable=False),
        sa.Column("remonttityyppi", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="oikotie"),
        sa.Column("invited_by_user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    with op.batch_alter_table("customer_invites", schema=None) as batch_op:
        batch_op.create_index(
            "ix_customer_invites_organization_id", ["organization_id"], unique=False
        )
        batch_op.create_index(
            "ix_customer_invites_token_hash", ["token_hash"], unique=True
        )


def downgrade():
    op.drop_table("customer_invites")
    op.drop_column("organizations", "enabled_features")
