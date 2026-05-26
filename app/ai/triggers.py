import logging

from flask import current_app

from app.ai.queue import get_enrichment_queue
from app.leads.models import Lead

logger = logging.getLogger(__name__)

ENRICHMENT_FIELD_NAMES = ("company", "website", "linkedin_url")


def has_enrichment_fields(lead: Lead) -> bool:
    for field in ENRICHMENT_FIELD_NAMES:
        value = getattr(lead, field, None)
        if value and str(value).strip():
            return True
    return False


def apply_enrichment_on_create(lead: Lead) -> None:
    """Set initial enrichment status and queue background job if applicable."""
    try:
        if not current_app.config.get("AI_ENRICHMENT_ENABLED"):
            lead.ai_enrichment_status = "disabled"
            lead.ai_enrichment_error = None
            return

        if not has_enrichment_fields(lead):
            lead.ai_enrichment_status = "disabled"
            lead.ai_enrichment_error = None
            return

        if not current_app.config.get("AI_AUTO_ENRICH_ON_CREATE"):
            lead.ai_enrichment_status = "disabled"
            return

        lead.ai_enrichment_status = "pending"
        lead.ai_enrichment_error = None
        _safe_enqueue(lead.id)
    except Exception:
        logger.exception("Failed to apply enrichment on create for lead %s", lead.id)


def apply_enrichment_on_update(lead: Lead, changed_fields: set[str] | None = None) -> None:
    """Queue enrichment when enrichment fields are added and lead is not enriched."""
    try:
        if not current_app.config.get("AI_ENRICHMENT_ENABLED"):
            return
        if not current_app.config.get("AI_AUTO_ENRICH_ON_CREATE"):
            return
        if lead.ai_enriched:
            return
        if lead.ai_enrichment_status == "processing":
            return

        relevant = set(ENRICHMENT_FIELD_NAMES)
        if changed_fields is not None and not (changed_fields & relevant):
            return

        if not has_enrichment_fields(lead):
            return

        lead.ai_enrichment_status = "pending"
        lead.ai_enrichment_error = None
        _safe_enqueue(lead.id)
    except Exception:
        logger.exception("Failed to apply enrichment on update for lead %s", lead.id)


def queue_manual_enrichment(lead: Lead) -> None:
    """Queue enrichment from a manual UI/API request."""
    if not current_app.config.get("AI_ENRICHMENT_ENABLED"):
        raise ValueError("ai_disabled")

    if not has_enrichment_fields(lead):
        raise ValueError("missing_fields")

    if lead.ai_enrichment_status == "processing":
        raise ValueError("already_processing")

    lead.ai_enrichment_status = "pending"
    lead.ai_enrichment_error = None
    _safe_enqueue(lead.id)


def _safe_enqueue(lead_id: int) -> None:
    try:
        get_enrichment_queue().enqueue(lead_id)
    except Exception:
        logger.exception("Failed to enqueue enrichment for lead %s", lead_id)
