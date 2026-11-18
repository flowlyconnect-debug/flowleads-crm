"""Limits on taking leads out of FlowLeads (CSV, CRM import file, CRM push),
so a customer cannot empty the database in one go to resell it.

- Only the customer's admins (and superadmins) may export; sellers may not.
- At most EXPORT_DAILY_ROWS rows a day per customer (Organization.export_daily_limit
  overrides it per customer, 0 = no limit). An export that would go over is
  refused as a whole and logged (`export_blocked`, seen on Asiakasseuranta).

Superadmins are never limited. EXPORT_LIMITS_ENABLED=false turns all of this off.
API reads are counted on Asiakasseuranta but not limited (integrations sync)."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import func

from app.extensions import db

EXPORT_ROLES = ("admin", "superadmin")
COUNTED = ("leads_exported", "crm_exported")


def user_may_export(user=None) -> bool:
    """Whether to offer export buttons to the user (the limit is checked on export)."""
    if user is None:
        from flask_login import current_user as user
    if not getattr(user, "is_authenticated", False):
        return False
    return not current_app.config.get("EXPORT_LIMITS_ENABLED", True) or user.role in EXPORT_ROLES


def daily_limit(organization_id: int) -> int:
    """Rows a day for the customer, 0 = no limit."""
    from app.users.models import Organization

    organization = db.session.get(Organization, organization_id)
    if organization is not None and organization.export_daily_limit is not None:
        return max(0, organization.export_daily_limit)
    return max(0, int(current_app.config.get("EXPORT_DAILY_ROWS", 200)))


def exported_today(organization_id: int, *, now: datetime | None = None) -> int:
    from app.admin.models import UsageDaily

    day = (now or datetime.now(timezone.utc)).date()
    total = (
        db.session.query(func.coalesce(func.sum(UsageDaily.count), 0))
        .filter(UsageDaily.organization_id == organization_id, UsageDaily.day == day, UsageDaily.metric.in_(COUNTED))
        .scalar()
    )
    return int(total or 0)


def check_export(user, organization_id: int, rows: int, *, kind: str, now: datetime | None = None) -> str | None:
    """None when the export may go ahead, else the Finnish reason shown to
    the user (the refusal is put in the audit log)."""
    from app.core.audit import log_audit

    if not current_app.config.get("EXPORT_LIMITS_ENABLED", True) or user.is_superadmin():
        return None
    reason = None
    if user.role not in EXPORT_ROLES:
        reason = "Liidien vienti on vain pääkäyttäjille. Pyydä organisaatiosi pääkäyttäjää tekemään vienti."
    else:
        limit = daily_limit(organization_id)
        done = exported_today(organization_id, now=now)
        if limit and done + rows > limit:
            left = max(0, limit - done)
            reason = (
                f"Päivän vientiraja on {limit} liidiä. Tänään on jo viety {done}, joten jäljellä on {left}. "
                f"Valitse enintään {left} liidiä tai ota yhteyttä FlowLeadsiin, jos tarvitset suuremman rajan."
                if left
                else f"Päivän vientiraja ({limit} liidiä) on täynnä. Vienti onnistuu taas huomenna, tai ota yhteyttä FlowLeadsiin."
            )
    if reason:
        log_audit(
            "export_blocked",
            user_id=user.id,
            organization_id=organization_id,
            target_type="lead",
            metadata={"kind": kind, "rows": rows, "role": user.role},
        )
        db.session.commit()
    return reason
