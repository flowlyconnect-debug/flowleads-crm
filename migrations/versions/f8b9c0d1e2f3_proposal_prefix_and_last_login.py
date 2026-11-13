"""Proposal number prefix per organization; users' last sign-in.

last_login_at is filled from the audit log, so users who have signed in
before are not shown as "invited".

Revision ID: f8b9c0d1e2f3
Revises: f7a8b9c0d1e2
"""

import sqlalchemy as sa
from alembic import op

revision = "f8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organization_settings") as batch:
        batch.add_column(sa.Column("proposal_number_prefix", sa.String(length=12), nullable=True))
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE users SET last_login_at = (SELECT MAX(audit_logs.created_at) FROM audit_logs "
        "WHERE audit_logs.user_id = users.id AND audit_logs.action = 'login_success')"
    )


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("last_login_at")
    with op.batch_alter_table("organization_settings") as batch:
        batch.drop_column("proposal_number_prefix")
