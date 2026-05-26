from app.extensions import db
from app.tasks.models import OrganizationSettings
from app.tasks.settings import get_organization_settings


def get_privacy_settings(organization_id: int) -> OrganizationSettings:
    return get_organization_settings(organization_id)


def privacy_settings_to_dict(settings: OrganizationSettings) -> dict:
    return {
        "gdpr_default_legal_basis": settings.gdpr_default_legal_basis,
        "gdpr_retention_days": settings.gdpr_retention_days,
        "gdpr_auto_anonymize_inactive": settings.gdpr_auto_anonymize_inactive,
        "privacy_policy_url": settings.privacy_policy_url,
        "data_controller_name": settings.data_controller_name,
        "data_controller_email": settings.data_controller_email,
    }


def update_privacy_settings(organization_id: int, data: dict) -> OrganizationSettings:
    settings = get_organization_settings(organization_id)
    if "gdpr_default_legal_basis" in data:
        val = data.get("gdpr_default_legal_basis")
        settings.gdpr_default_legal_basis = (val or "").strip() or None
    if "gdpr_retention_days" in data:
        try:
            days = int(data.get("gdpr_retention_days") or 730)
        except (TypeError, ValueError):
            days = 730
        settings.gdpr_retention_days = max(1, min(days, 3650))
    if "gdpr_auto_anonymize_inactive" in data:
        settings.gdpr_auto_anonymize_inactive = bool(data.get("gdpr_auto_anonymize_inactive"))
    if "privacy_policy_url" in data:
        val = data.get("privacy_policy_url")
        settings.privacy_policy_url = (val or "").strip()[:500] or None
    if "data_controller_name" in data:
        val = data.get("data_controller_name")
        settings.data_controller_name = (val or "").strip()[:255] or None
    if "data_controller_email" in data:
        val = data.get("data_controller_email")
        settings.data_controller_email = (val or "").strip()[:255] or None
    db.session.flush()
    return settings
