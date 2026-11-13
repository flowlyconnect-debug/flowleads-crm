"""Server-side store for secrets shown once (new API key, temporary password).

Revision ID: f7a8b9c0d1e2
Revises: f6f7a8b9c0d1
"""

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "f6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "one_time_secrets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_one_time_secrets_token_hash"),
    )
    op.create_index("ix_one_time_secrets_user_id", "one_time_secrets", ["user_id"])
    op.create_index("ix_one_time_secrets_expires_at", "one_time_secrets", ["expires_at"])


def downgrade():
    op.drop_index("ix_one_time_secrets_expires_at", table_name="one_time_secrets")
    op.drop_index("ix_one_time_secrets_user_id", table_name="one_time_secrets")
    op.drop_table("one_time_secrets")
