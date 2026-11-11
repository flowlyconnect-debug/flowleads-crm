"""Toimialat a customer is entitled to (organizations.remonttityypit) and the
toimialat picked on an invite (customer_invites.remonttityypit)

Revision ID: h6c7d8e9f0a1
Revises: g5b6c7d8e9f0

Backfill: an organization gets the toimialat its search profiles use today,
or those of its newest open invite. Organizations with neither stay NULL
(= not restricted); a superadmin sets them on /admin/customers.
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "h6c7d8e9f0a1"
down_revision = "g5b6c7d8e9f0"
branch_labels = None
depends_on = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _as_list(value) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [v for v in value if v] if isinstance(value, list) else []


def upgrade():
    op.add_column("organizations", sa.Column("remonttityypit", sa.JSON(), nullable=True))
    op.add_column("customer_invites", sa.Column("remonttityypit", json_type, nullable=True))

    bind = op.get_bind()
    wanted: dict[int, list[str]] = {}
    profiles = bind.execute(
        sa.text("SELECT organization_id, remonttityyppi, remonttityypit FROM search_profiles ORDER BY id")
    ).fetchall()
    for org_id, primary, types in profiles:
        for name in _as_list(types) or [primary]:
            if name and name not in wanted.setdefault(org_id, []):
                wanted[org_id].append(name)
    invites = bind.execute(
        sa.text(
            "SELECT organization_id, remonttityyppi FROM customer_invites "
            "WHERE accepted_at IS NULL AND revoked_at IS NULL ORDER BY id"
        )
    ).fetchall()
    for org_id, primary in invites:
        if org_id not in wanted and primary:
            wanted[org_id] = [primary]

    organizations = sa.table("organizations", sa.column("id", sa.Integer), sa.column("remonttityypit", sa.JSON))
    for org_id, types in wanted.items():
        bind.execute(organizations.update().where(organizations.c.id == org_id).values(remonttityypit=types))


def downgrade():
    with op.batch_alter_table("customer_invites") as batch:
        batch.drop_column("remonttityypit")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("remonttityypit")
