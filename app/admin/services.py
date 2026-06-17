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


def get_dashboard_stats() -> dict:
    orgs = get_accessible_organizations()
    user_count = 0
    if current_user.role == "superadmin":
        user_count = User.query.count()
    elif current_user.organization_id:
        user_count = User.query.filter_by(organization_id=current_user.organization_id).count()

    stats = {
        "organization_count": len(orgs),
        "user_count": user_count,
    }
    if current_user.role == "superadmin":
        stats["ai_usage"] = get_ai_usage_stats()
    return stats


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
    customers = []
    for org in get_accessible_organizations():
        profiles = list_profiles(org.id)
        profile = profiles[0] if profiles else None
        customers.append(
            {
                "org": org,
                "profile_name": profile.name if profile else None,
                "lead_count": Lead.query.filter_by(organization_id=org.id).count(),
                "last_search_label": _format_last_search(
                    profile.last_run_at if profile else None
                ),
                "is_active": org.is_active,
            }
        )
    return customers
