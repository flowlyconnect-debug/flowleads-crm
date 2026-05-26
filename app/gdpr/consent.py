from datetime import datetime, timezone

from app.core.audit import log_audit
from app.leads.models import GDPR_CONSENT_SOURCES, GDPR_LEGAL_BASES, Lead


def apply_gdpr_consent_fields(
    lead: Lead,
    data: dict,
    *,
    user_id: int | None = None,
    organization_id: int | None = None,
    consent_source: str = "api",
) -> bool:
    """Apply GDPR consent fields from API/UI payload. Returns True if consent changed."""
    changed_consent = False
    org_id = organization_id or lead.organization_id

    if "gdpr_consent" in data:
        new_val = bool(data.get("gdpr_consent"))
        if lead.gdpr_consent != new_val:
            lead.gdpr_consent = new_val
            lead.gdpr_consent_at = datetime.now(timezone.utc) if new_val else None
            if new_val:
                src = data.get("gdpr_consent_source") or consent_source
                if src in GDPR_CONSENT_SOURCES:
                    lead.gdpr_consent_source = src
            else:
                lead.gdpr_consent_source = None
            changed_consent = True
            log_audit(
                "gdpr_consent_given" if new_val else "gdpr_consent_withdrawn",
                user_id=user_id,
                organization_id=org_id,
                target_type="lead",
                target_id=lead.id,
            )

    if "gdpr_legal_basis" in data:
        basis = data.get("gdpr_legal_basis")
        if basis is None or basis == "":
            lead.gdpr_legal_basis = None
        elif basis in GDPR_LEGAL_BASES:
            lead.gdpr_legal_basis = basis

    if "marketing_opt_in" in data:
        new_opt = bool(data.get("marketing_opt_in"))
        if lead.marketing_opt_in != new_opt:
            lead.marketing_opt_in = new_opt
            if not new_opt and not lead.unsubscribed:
                lead.unsubscribed = True
                lead.unsubscribed_at = datetime.now(timezone.utc)

    return changed_consent
