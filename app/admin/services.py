from datetime import datetime, timedelta, timezone

from flask_login import current_user

from app.leads.models import Activity, Lead
from app.search.profile_services import list_profiles
from app.users.models import Organization, User
from app.users.services import UserServiceError, ensure_same_organization


def get_accessible_organizations() -> list[Organization]:
    if current_user.role == "superadmin":
        return Organization.query.order_by(Organization.name).all()
    if current_user.organization_id is None:
        return []
    org = Organization.query.get(current_user.organization_id)
    return [org] if org else []


def get_organization_users(organization_id: int) -> list[User]:
    org = Organization.query.get(organization_id)
    if org is None:
        raise UserServiceError("Organization not found.", "not_found")

    if current_user.role != "superadmin":
        if current_user.organization_id != organization_id:
            raise UserServiceError("Cross-tenant access denied.", "cross_tenant")

    return User.query.filter_by(organization_id=organization_id).order_by(User.email).all()


def get_ai_usage_stats() -> dict:
    completed = Activity.query.filter_by(type="ai_enriched").all()
    total_enrichments = 0
    total_tokens = 0
    failed_count = Lead.query.filter_by(ai_enrichment_status="failed").count()

    for activity in completed:
        meta = activity.metadata_json or {}
        if meta.get("failed"):
            continue
        total_enrichments += 1
        total_tokens += int(meta.get("total_tokens") or 0)

    return {
        "total_enrichments": total_enrichments,
        "total_tokens": total_tokens,
        "failed_count": failed_count,
    }


def _format_last_search(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    now = datetime.now(timezone.utc)
    if dt.date() == now.date():
        return "Tänään"
    yesterday = (now - timedelta(days=1)).date()
    if dt.date() == yesterday:
        return "Eilen"
    return dt.strftime("%d.%m.%Y")


def list_customers_summary() -> list[dict]:
    """One row per organization with ALL of its search profiles."""
    from sqlalchemy import func

    from app.admin.models import CustomerInvite
    from app.extensions import db
    from app.listings.target_services import count_reserve, count_targets

    customers = []
    for org in get_accessible_organizations():
        profiles = list_profiles(org.id)
        # newest lead of the organization (profile.last_run_at is only set by
        # the old automatic lead creation, so the column was always empty)
        last_run = db.session.query(func.max(Lead.created_at)).filter(Lead.organization_id == org.id).scalar()
        open_invite = (
            CustomerInvite.query.filter_by(organization_id=org.id, accepted_at=None, revoked_at=None)
            .order_by(CustomerInvite.id.desc())
            .first()
        )
        # targets waiting on the customer's Kohteet page
        pending = count_targets(org.id) if profiles else 0
        admin = (
            User.query.filter_by(organization_id=org.id, role="admin").order_by(User.id.asc()).first()
        )
        first = profiles[0] if profiles else None
        customers.append(
            {
                "org": org,
                "profiles": profiles,
                "profile_name": first.name if first else None,
                "profile_id": first.id if first else None,
                "profile_is_active": first.is_active if first else None,
                "lead_count": Lead.query.filter_by(organization_id=org.id).count(),
                "pending_matches": pending,
                "reserve_matches": count_reserve(org.id) if profiles else 0,
                "open_invite": open_invite,
                "last_search_label": _format_last_search(last_run),
                "is_active": org.is_active,
                "admin_email": admin.email if admin else None,
                "password_pending": bool(admin and admin.must_change_password),
                "enabled_features": list(org.enabled_features or []),
            }
        )
    return customers
