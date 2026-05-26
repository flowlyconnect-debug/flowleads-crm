from datetime import datetime

from app.leads.models import Lead, PipelineStage


def serialize_stage(stage: PipelineStage) -> dict:
    return {
        "id": stage.id,
        "name": stage.name,
        "order_index": stage.order_index,
        "color": stage.color,
        "is_default": stage.is_default,
    }


def serialize_lead(lead: Lead) -> dict:
    stage_name = lead.stage.name if lead.stage else None
    return {
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
        "tags": list(lead.tags or []),
        "score": lead.score,
        "status": lead.status,
        "stage": stage_name,
        "created_at": _iso(lead.created_at),
        "updated_at": _iso(lead.updated_at),
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
