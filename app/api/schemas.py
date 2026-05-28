from datetime import datetime

from app.custom_fields.models import CustomFieldDefinition
from app.leads.models import Lead, PipelineStage


def serialize_custom_field_definition(defn: CustomFieldDefinition) -> dict:
    return {
        "id": defn.id,
        "entity_type": defn.entity_type,
        "name": defn.name,
        "label": defn.label,
        "field_type": defn.field_type,
        "options": defn.options,
        "is_required": defn.is_required,
        "is_searchable": defn.is_searchable,
        "order_index": defn.order_index,
        "created_at": _iso(defn.created_at),
    }


def serialize_segment(segment) -> dict:
    return {
        "id": segment.id,
        "name": segment.name,
        "description": segment.description,
        "filters": segment.filters,
        "is_pinned": segment.is_pinned,
        "lead_count": segment.lead_count_cache,
        "created_at": _iso(segment.created_at),
        "updated_at": _iso(segment.updated_at),
    }


def serialize_stage(stage: PipelineStage) -> dict:
    return {
        "id": stage.id,
        "name": stage.name,
        "order_index": stage.order_index,
        "color": stage.color,
        "is_default": stage.is_default,
    }


def serialize_lead(lead: Lead, *, custom_fields: dict | None = None) -> dict:
    stage_name = lead.stage.name if lead.stage else None
    payload = {
        "id": lead.id,
        "email": lead.email,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "company": lead.company,
        "title": lead.title,
        "phone": lead.phone,
        "website": lead.website,
        "linkedin_url": lead.linkedin_url,
        "source": lead.source,
        "source_ref": lead.source_ref,
        "industry": lead.industry,
        "region": lead.region,
        "tags": list(lead.tags or []),
        "score": lead.score,
        "status": lead.status,
        "stage": stage_name,
        "created_at": _iso(lead.created_at),
        "updated_at": _iso(lead.updated_at),
        "gdpr_consent": lead.gdpr_consent,
        "gdpr_consent_at": _iso(lead.gdpr_consent_at),
        "gdpr_consent_source": lead.gdpr_consent_source,
        "gdpr_legal_basis": lead.gdpr_legal_basis,
        "marketing_opt_in": lead.marketing_opt_in,
        "unsubscribed": lead.unsubscribed,
        "unsubscribed_at": _iso(lead.unsubscribed_at),
        "is_anonymized": lead.is_anonymized,
        "anonymized_at": _iso(lead.anonymized_at),
    }
    if custom_fields is not None:
        payload["custom_fields"] = custom_fields
    return payload


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
