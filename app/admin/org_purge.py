"""Permanently delete a customer organization and everything that belongs to
it: users, search profiles, targets, leads, tasks, proposals, e-mail logs,
settings. The shared listing catalogue and superadmin accounts stay.

Superadmin UI (/admin/customers, typed-name confirmation) and
`flask purge-organizations` (dry run unless --yes). Audit-log rows are kept with
the organization / user reference cleared.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from app.extensions import db
from app.leads.purge import _purge_table
from app.users.models import Organization, User

# Nullable organization / user references whose rows mean nothing without it.
# (A NULL email_templates.organization_id would turn the row into a system template.)
_ORG_OWNED = frozenset(
    {
        ("users", "organization_id"),
        ("email_templates", "organization_id"),
        ("tasks", "lead_id"),
        ("listing_matches", "lead_id"),
    }
)


class OrganizationPurgeError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def purge_organization(organization_id: int, *, dry_run: bool = True) -> dict:
    """Returns {table: affected rows}. Does not commit; dry_run changes nothing."""
    organization = db.session.get(Organization, organization_id)
    if organization is None:
        raise OrganizationPurgeError("Organization not found.")
    if User.query.filter_by(organization_id=organization_id, role="superadmin").first() is not None:
        raise OrganizationPurgeError("The organization has a superadmin; it cannot be deleted.")

    tables = db.metadata.tables
    counts: dict[str, int] = {}

    # custom field values point at their entity by type + id, not by foreign key
    values = tables.get("custom_field_values")
    if values is not None and "organization_id" in values.c:
        condition = values.c.organization_id == organization_id
        counts["custom_field_values"] = len(db.session.execute(select(values.c.id).where(condition)).all())
        if not dry_run:
            db.session.execute(delete(values).where(condition), execution_options={"synchronize_session": False})

    _purge_table(
        tables["organizations"],
        tables["organizations"].c.id == organization_id,
        counts,
        dry_run,
        delete_with_parent=_ORG_OWNED,
    )
    if not dry_run:
        db.session.expire_all()
    return {name: n for name, n in counts.items() if n}


def customer_organization_ids() -> list[int]:
    """Every organization without a superadmin in it."""
    with_superadmin = select(User.organization_id).where(
        User.role == "superadmin", User.organization_id.isnot(None)
    )
    return [
        org_id
        for (org_id,) in db.session.query(Organization.id)
        .filter(Organization.id.notin_(with_superadmin))
        .order_by(Organization.id)
        .all()
    ]
