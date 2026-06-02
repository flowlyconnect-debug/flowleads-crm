from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.api.models import APIKey
from app.api.schemas import serialize_lead
from app.custom_fields.services import CustomFieldService, CustomFieldServiceError
from app.core.audit import log_audit
from app.core.security import generate_api_key, normalize_email, validate_email
from app.extensions import db
from app.leads.models import LEAD_SOURCES, Lead, PipelineStage
from app.ai.triggers import apply_enrichment_on_create, apply_enrichment_on_update
from app.leads.services import (
    LeadService,
    LeadServiceError,
    get_default_stage,
    get_lead_for_org,
)
from app.leads.validators import (
    normalize_lead_data,
    normalize_tags,
    validate_lead_fields,
    validate_lead_source,
    validate_score,
    validate_url_field,
)
from app.users.models import Organization

API_UPDATABLE_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "company",
    "title",
    "website",
    "linkedin_url",
    "source",
    "source_ref",
    "tags",
    "notes",
    "score",
    "score_reason",
)

API_PATCH_FORBIDDEN = frozenset(
    {"organization_id", "assigned_to", "stage_id", "status", "id", "stage"}
)

MAX_BULK_LEADS = 100
MAX_API_TAGS = 50
MAX_API_TAG_LENGTH = 50


class ApiServiceError(Exception):
    def __init__(self, message: str, code: str = "validation_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def validate_api_tags(tags) -> tuple[bool, str | None]:
    normalized = normalize_tags(tags)
    if len(normalized) > MAX_API_TAGS:
        return False, f"At most {MAX_API_TAGS} tags are allowed."
    for tag in normalized:
        if len(tag) > MAX_API_TAG_LENGTH:
            return False, f"Each tag must be at most {MAX_API_TAG_LENGTH} characters."
    return True, None


def validate_api_lead_payload(payload: dict, *, require_email: bool = False) -> dict:
    if not isinstance(payload, dict):
        raise ApiServiceError("Request body must be a JSON object.", "validation_error")

    data = normalize_lead_data(payload)

    if require_email:
        email = data.get("email")
        if not email:
            raise ApiServiceError("Email is required.", "validation_error")
        if not validate_email(email):
            raise ApiServiceError("Invalid email address.", "validation_error")

    if data.get("email") and not validate_email(data.get("email")):
        raise ApiServiceError("Invalid email address.", "validation_error")

    source = data.get("source") or "n8n"
    if not validate_lead_source(source):
        raise ApiServiceError(
            f"Invalid source. Allowed: {', '.join(LEAD_SOURCES)}.",
            "validation_error",
        )
    data["source"] = source

    ok, msg = validate_lead_fields(data, require_identifier=require_email)
    if not ok:
        raise ApiServiceError(msg or "Validation failed.", "validation_error")

    ok, msg = validate_api_tags(data.get("tags", []))
    if not ok:
        raise ApiServiceError(msg, "validation_error")

    if data.get("website") and not validate_url_field(data["website"]):
        raise ApiServiceError("Invalid website URL.", "validation_error")
    if data.get("linkedin_url") and not validate_url_field(data["linkedin_url"]):
        raise ApiServiceError("Invalid LinkedIn URL.", "validation_error")

    return data


def find_lead_for_upsert(
    organization_id: int,
    *,
    email: str | None,
    source: str,
    source_ref: str | None,
) -> Lead | None:
    ref = str(source_ref).strip() if source_ref else None
    if source and ref:
        lead = Lead.query.filter_by(
            organization_id=organization_id,
            source=source,
            source_ref=ref,
        ).first()
        if lead:
            return lead

    if email:
        normalized = normalize_email(email)
        if normalized:
            return Lead.query.filter_by(
                organization_id=organization_id,
                email=normalized,
            ).first()
    return None


def _merge_tags(existing: list | None, incoming: list | None) -> list[str]:
    merged = []
    seen = set()
    for tag in list(existing or []) + list(incoming or []):
        key = str(tag).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(key)
    return merged


def _apply_metadata(lead: Lead, metadata: dict | None) -> None:
    if not metadata or not isinstance(metadata, dict):
        return
    current = dict(lead.ai_contact_info or {})
    current.update(metadata)
    lead.ai_contact_info = current


def _apply_partial_update(lead: Lead, data: dict) -> bool:
    changed = False
    scalar_fields = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "company",
        "title",
        "website",
        "linkedin_url",
        "source",
        "source_ref",
        "notes",
        "score",
        "score_reason",
    )
    for field in scalar_fields:
        if field not in data:
            continue
        value = data[field]
        if value is None:
            continue
        if field == "source" and not validate_lead_source(value):
            raise ApiServiceError("Invalid lead source.", "validation_error")
        if field == "score":
            ok, msg = validate_score(value)
            if not ok:
                raise ApiServiceError(msg, "validation_error")
        if getattr(lead, field) != value:
            setattr(lead, field, value)
            changed = True

    if "tags" in data and data["tags"] is not None:
        new_tags = _merge_tags(lead.tags, normalize_tags(data["tags"]))
        if new_tags != (lead.tags or []):
            lead.tags = new_tags
            changed = True

    if "metadata" in data and data["metadata"] is not None:
        _apply_metadata(lead, data["metadata"])
        changed = True

    gdpr_keys = {"gdpr_consent", "gdpr_legal_basis", "marketing_opt_in"}
    if gdpr_keys & set(data.keys()):
        from app.gdpr.consent import apply_gdpr_consent_fields

        if apply_gdpr_consent_fields(lead, data, consent_source="api"):
            changed = True
        elif any(k in data for k in ("gdpr_legal_basis", "marketing_opt_in")):
            changed = True

    return changed


def _apply_custom_fields_from_payload(
    lead: Lead, organization_id: int, payload: dict, *, partial: bool = False
) -> None:
    custom = payload.get("custom_fields")
    if not custom:
        return
    if not isinstance(custom, dict):
        raise ApiServiceError("custom_fields must be a JSON object.", "validation_error")
    try:
        CustomFieldService.set_values_by_name(
            lead.id, "lead", custom, organization_id, partial=partial
        )
    except CustomFieldServiceError as exc:
        raise ApiServiceError(exc.message, exc.code) from exc


def upsert_lead(organization_id: int, payload: dict) -> tuple[Lead, str]:
    raw_payload = payload if isinstance(payload, dict) else {}
    data = validate_api_lead_payload(payload, require_email=True)
    email = data.get("email")
    source = data.get("source", "n8n")
    source_ref = data.get("source_ref")

    existing = find_lead_for_upsert(
        organization_id,
        email=email,
        source=source,
        source_ref=source_ref,
    )

    if existing:
        changed = _apply_partial_update(existing, data)
        existing.updated_at = datetime.now(timezone.utc)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            raise ApiServiceError(
                "A lead with this source and reference already exists.",
                "validation_error",
            ) from None

        if changed:
            LeadService.log_activity(
                existing.id,
                None,
                "updated",
                content="Updated via API",
            )
            apply_enrichment_on_update(existing, set(data.keys()))
        _apply_custom_fields_from_payload(
            existing, organization_id, raw_payload, partial=True
        )
        if existing.company_id is None and existing.company:
            company_name = str(existing.company).strip()
            if company_name:
                from app.companies.models import Company

                existing_company = (
                    Company.query.filter(
                        Company.organization_id == organization_id,
                        func.lower(Company.name) == func.lower(company_name),
                    ).first()
                )
                if existing_company:
                    existing.company_id = existing_company.id
                else:
                    new_company = Company(
                        name=company_name,
                        organization_id=organization_id,
                        type="prospect",
                    )
                    db.session.add(new_company)
                    db.session.flush()
                    existing.company_id = new_company.id
        return _load_lead(existing.id, organization_id), "updated"

    stage = get_default_stage(organization_id)
    lead = Lead(
        organization_id=organization_id,
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
        email=email,
        phone=data.get("phone"),
        company=data.get("company"),
        title=data.get("title"),
        website=data.get("website"),
        linkedin_url=data.get("linkedin_url"),
        stage_id=stage.id,
        status="active",
        source=source,
        source_ref=source_ref,
        tags=normalize_tags(data.get("tags", [])),
    )
    if data.get("metadata"):
        _apply_metadata(lead, data["metadata"])

    db.session.add(lead)
    try:
        db.session.flush()
        gdpr_keys = {"gdpr_consent", "gdpr_legal_basis", "marketing_opt_in"}
        if gdpr_keys & set(raw_payload.keys()):
            from app.gdpr.consent import apply_gdpr_consent_fields

            apply_gdpr_consent_fields(lead, raw_payload, consent_source="api")

        if lead.company_id is None and lead.company:
            company_name = str(lead.company).strip()
            if company_name:
                from app.companies.models import Company

                existing_company = (
                    Company.query.filter(
                        Company.organization_id == organization_id,
                        func.lower(Company.name) == func.lower(company_name),
                    ).first()
                )
                if existing_company:
                    lead.company_id = existing_company.id
                else:
                    new_company = Company(
                        name=company_name,
                        organization_id=organization_id,
                        type="prospect",
                    )
                    db.session.add(new_company)
                    db.session.flush()
                    lead.company_id = new_company.id
    except IntegrityError:
        db.session.rollback()
        dup = find_lead_for_upsert(
            organization_id,
            email=email,
            source=source,
            source_ref=source_ref,
        )
        if dup:
            changed = _apply_partial_update(dup, data)
            dup.updated_at = datetime.now(timezone.utc)
            db.session.flush()
            if changed:
                LeadService.log_activity(
                    dup.id, None, "updated", content="Updated via API"
                )
                apply_enrichment_on_update(dup, set(data.keys()))
            _apply_custom_fields_from_payload(
                dup, organization_id, raw_payload, partial=True
            )
            return _load_lead(dup.id, organization_id), "updated"
        raise ApiServiceError(
            "A lead with this source and reference already exists.",
            "validation_error",
        ) from None

    LeadService.log_activity(lead.id, None, "created", content="Created via API")
    apply_enrichment_on_create(lead)
    from app.tasks.services import TaskService

    try:
        TaskService.create_auto_tasks(lead, "new_lead")
    except Exception:
        pass
    try:
        from app.sequences.services import SequenceService

        SequenceService.trigger_auto_enroll(lead, "on_lead_created")
    except Exception:
        pass
    from app.automations.triggers import fire_automation_trigger

    fire_automation_trigger(
        "lead_created",
        {"lead_id": lead.id},
        organization_id,
    )
    _apply_custom_fields_from_payload(lead, organization_id, raw_payload, partial=True)
    return _load_lead(lead.id, organization_id), "created"


def _load_lead(lead_id: int, organization_id: int | None = None) -> Lead:
    query = Lead.query.options(joinedload(Lead.stage)).filter_by(id=lead_id)
    if organization_id is not None:
        query = query.filter_by(organization_id=organization_id)
    lead = query.first()
    if not lead:
        raise ApiServiceError("Lead not found.", "not_found")
    return lead


def _serialize_lead_with_custom_fields(lead: Lead, organization_id: int) -> dict:
    custom = CustomFieldService.get_values(lead.id, "lead", organization_id)
    return serialize_lead(lead, custom_fields=custom)


def bulk_upsert_leads(organization_id: int, items: list) -> dict:
    if len(items) > MAX_BULK_LEADS:
        raise ApiServiceError(
            f"At most {MAX_BULK_LEADS} leads per request.",
            "validation_error",
        )

    created = 0
    updated = 0
    errors = []
    seen_emails: dict[str, int] = {}

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(
                {
                    "index": index,
                    "code": "validation_error",
                    "message": "Each item must be a JSON object.",
                }
            )
            continue

        email = normalize_email(item.get("email")) if item.get("email") else None
        if email:
            if email in seen_emails:
                errors.append(
                    {
                        "index": index,
                        "code": "validation_error",
                        "message": "Duplicate email in bulk payload.",
                    }
                )
                continue
            seen_emails[email] = index

        try:
            lead, action = upsert_lead(organization_id, item)
            db.session.commit()
            if action == "created":
                created += 1
            else:
                updated += 1
        except ApiServiceError as exc:
            db.session.rollback()
            errors.append(
                {"index": index, "code": exc.code, "message": exc.message}
            )
        except Exception:
            db.session.rollback()
            errors.append(
                {
                    "index": index,
                    "code": "validation_error",
                    "message": "Failed to process lead.",
                }
            )

    return {"created": created, "updated": updated, "errors": errors}


def patch_lead(organization_id: int, lead_id: int, payload: dict) -> Lead:
    if not isinstance(payload, dict):
        raise ApiServiceError("Request body must be a JSON object.", "validation_error")

    forbidden = set(payload.keys()) & API_PATCH_FORBIDDEN
    if forbidden:
        raise ApiServiceError(
            f"Fields not allowed: {', '.join(sorted(forbidden))}.",
            "validation_error",
        )

    try:
        lead = get_lead_for_org(lead_id, organization_id)
    except LeadServiceError:
        raise ApiServiceError("Lead not found.", "not_found") from None

    if lead.status == "archived":
        raise ApiServiceError("Cannot update archived lead.", "validation_error")

    data = normalize_lead_data(payload)
    ok, msg = validate_lead_fields(data, require_identifier=False)
    if not ok:
        raise ApiServiceError(msg, "validation_error")

    ok, msg = validate_api_tags(data.get("tags", lead.tags))
    if not ok:
        raise ApiServiceError(msg, "validation_error")

    changed = _apply_partial_update(lead, data)
    if changed:
        lead.updated_at = datetime.now(timezone.utc)
        LeadService.log_activity(lead.id, None, "updated", content="Updated via API")
        db.session.flush()
        apply_enrichment_on_update(lead, set(data.keys()))

    if "custom_fields" in payload:
        _apply_custom_fields_from_payload(lead, organization_id, payload, partial=True)

    return lead


def list_leads_api(organization_id: int, query_args: dict) -> dict:
    page = max(1, int(query_args.get("page", 1) or 1))
    per_page = max(1, min(100, int(query_args.get("per_page", 25) or 25)))

    filters: dict = {}
    status = query_args.get("status")
    if status:
        filters["status"] = status
    elif query_args.get("include_archived", "").lower() not in ("1", "true", "yes"):
        filters["status"] = None

    if query_args.get("stage"):
        stage_name = query_args.get("stage").strip()
        stage = PipelineStage.query.filter_by(
            organization_id=organization_id,
            name=stage_name,
        ).first()
        if stage:
            filters["stage_id"] = stage.id

    if query_args.get("source"):
        filters["source"] = query_args.get("source").strip()

    for flag in ("gdpr_consent", "marketing_opt_in", "unsubscribed", "is_anonymized"):
        raw = query_args.get(flag)
        if raw is not None and str(raw).lower() in ("1", "true", "yes"):
            filters[flag] = True

    for param, key in (("created_after", "created_from"), ("created_before", "created_to")):
        raw = query_args.get(param)
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            filters[key] = parsed
        except ValueError:
            raise ApiServiceError(f"Invalid date format for {param}.", "validation_error")

    pagination = LeadService.search(organization_id, filters, page=page, per_page=per_page)
    lead_ids = [item.id for item in pagination.items]
    custom_by_lead = CustomFieldService.get_values_bulk(lead_ids, "lead", organization_id)
    leads = [
        serialize_lead(item, custom_fields=custom_by_lead.get(item.id, {}))
        for item in pagination.items
    ]
    return {
        "leads": leads,
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
    }


def enrich_lead_api(organization_id: int, lead_id: int) -> Lead:
    from app.ai.triggers import queue_manual_enrichment

    if not current_app.config.get("AI_ENRICHMENT_ENABLED"):
        raise ApiServiceError("AI enrichment is disabled.", "ai_disabled")

    try:
        lead = get_lead_for_org(lead_id, organization_id)
    except LeadServiceError:
        raise ApiServiceError("Lead not found.", "not_found") from None

    try:
        queue_manual_enrichment(lead)
        db.session.flush()
    except ValueError as exc:
        code = str(exc)
        if code == "ai_disabled":
            raise ApiServiceError("AI enrichment is disabled.", "ai_disabled") from None
        if code == "missing_fields":
            raise ApiServiceError(
                "Lead must have company, website, or LinkedIn URL.",
                "validation_error",
            ) from None
        if code == "already_processing":
            raise ApiServiceError("Enrichment is already in progress.", "validation_error") from None
        raise ApiServiceError("Could not queue enrichment.", "validation_error") from None

    return lead


def get_lead_api(organization_id: int, lead_id: int) -> Lead:
    try:
        return get_lead_for_org(lead_id, organization_id)
    except LeadServiceError:
        raise ApiServiceError("Lead not found.", "not_found") from None


def create_lead_task_api(organization_id: int, lead_id: int, payload: dict) -> dict:
    from app.tasks.services import TaskService, TaskServiceError

    if not isinstance(payload, dict):
        raise ApiServiceError("Request body must be a JSON object.", "validation_error")

    try:
        get_lead_for_org(lead_id, organization_id)
    except LeadServiceError:
        raise ApiServiceError("Lead not found.", "not_found") from None

    data = dict(payload)
    if "due_date" not in data:
        raise ApiServiceError("due_date is required.", "validation_error")
    if not (data.get("title") or "").strip():
        data["title"] = "Follow up"

    try:
        task = TaskService.create(
            data,
            organization_id,
            None,
            lead_id=lead_id,
        )
    except TaskServiceError as exc:
        raise ApiServiceError(exc.message, exc.code) from None

    return {
        "id": task.id,
        "title": task.title,
        "type": task.type,
        "status": task.status,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "lead_id": task.lead_id,
        "assigned_to": task.assigned_to,
    }


def list_pipeline_stages(organization_id: int) -> list[PipelineStage]:
    return (
        PipelineStage.query.filter_by(organization_id=organization_id)
        .order_by(PipelineStage.order_index.asc())
        .all()
    )


# --- API key management ---


class APIKeyServiceError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def create_api_key(
    organization_id: int,
    name: str,
    *,
    created_by: int | None = None,
    expires_at: datetime | None = None,
    test_mode: bool = False,
) -> tuple[APIKey, str]:
    org = db.session.get(Organization, organization_id)
    if not org:
        raise APIKeyServiceError("Organization not found.")

    name = (name or "").strip()
    if not name or len(name) > 100:
        raise APIKeyServiceError("Name is required (max 100 characters).")

    full_key, key_hash, key_prefix = generate_api_key(test_mode=test_mode)
    api_key = APIKey(
        organization_id=organization_id,
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        created_by=created_by,
        expires_at=expires_at,
    )
    db.session.add(api_key)
    db.session.flush()

    log_audit(
        "api_key_created",
        user_id=created_by,
        organization_id=organization_id,
        target_type="api_key",
        target_id=api_key.id,
        metadata={"name": name, "key_prefix": key_prefix},
    )
    return api_key, full_key


def revoke_api_key(
    api_key_id: int,
    *,
    revoked_by: int | None = None,
    organization_id: int | None = None,
) -> APIKey:
    api_key = db.session.get(APIKey, api_key_id)
    if not api_key:
        raise APIKeyServiceError("API key not found.")
    if organization_id is not None and api_key.organization_id != organization_id:
        raise APIKeyServiceError("API key not found.")

    if api_key.revoked_at is not None:
        return api_key

    api_key.is_active = False
    api_key.revoked_at = datetime.now(timezone.utc)
    api_key.revoked_by = revoked_by
    db.session.flush()

    log_audit(
        "api_key_revoked",
        user_id=revoked_by,
        organization_id=api_key.organization_id,
        target_type="api_key",
        target_id=api_key.id,
        metadata={"name": api_key.name, "key_prefix": api_key.key_prefix},
    )
    return api_key


def list_api_keys(organization_id: int | None = None) -> list[APIKey]:
    query = APIKey.query.options(joinedload(APIKey.organization))
    if organization_id is not None:
        query = query.filter_by(organization_id=organization_id)
    return query.order_by(APIKey.created_at.desc()).all()


def rotate_api_key(
    api_key_id: int,
    *,
    rotated_by: int | None = None,
    test_mode: bool = False,
) -> tuple[APIKey, str]:
    """Revoke existing key and create a new one for the same organization/name."""
    old_key = db.session.get(APIKey, api_key_id)
    if not old_key:
        raise APIKeyServiceError("API key not found.")

    org_id = old_key.organization_id
    name = old_key.name
    revoke_api_key(api_key_id, revoked_by=rotated_by)
    return create_api_key(
        org_id,
        name,
        created_by=rotated_by,
        test_mode=test_mode or bool(current_app.config.get("TESTING")),
    )
