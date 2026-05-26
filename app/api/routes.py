from flask import Blueprint, g, request

from app.api.auth import require_api_key
from app.api.rate_limit import api_rate_limit, api_rate_limit_key
from app.api.crm_api import (
    create_custom_field_definition,
    create_segment_api,
    delete_custom_field_definition,
    delete_segment_api,
    get_segment_api,
    list_custom_field_definitions,
    list_segment_leads_api,
    list_segments_api,
    update_custom_field_definition,
    update_segment_api,
)
from app.api.schemas import (
    serialize_custom_field_definition,
    serialize_lead,
    serialize_segment,
    serialize_stage,
)
from app.api.services import (
    ApiServiceError,
    _serialize_lead_with_custom_fields,
    bulk_upsert_leads,
    create_lead_task_api,
    enrich_lead_api,
    get_lead_api,
    list_leads_api,
    list_pipeline_stages,
    patch_lead,
    upsert_lead,
)
from app.core.errors import json_error, json_success
from app.extensions import db, limiter

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

API_VERSION = "1.0.0"


@api_bp.route("/health", methods=["GET"])
def health():
    return json_success({"status": "ok", "version": API_VERSION})


@api_bp.route("/me", methods=["GET"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def me():
    org = g.organization
    return json_success(
        {
            "organization": {
                "id": org.id,
                "name": org.name,
                "slug": org.slug,
            },
            "key_name": g.key_name,
            "key_prefix": g.api_key.key_prefix,
        }
    )


@api_bp.route("/leads", methods=["POST"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def create_lead():
    if not request.is_json:
        return json_error("validation_error", "JSON body is required.", 400)

    try:
        lead, action = upsert_lead(g.organization_id, request.get_json(silent=True))
        db.session.commit()
    except ApiServiceError as exc:
        db.session.rollback()
        status = 404 if exc.code == "not_found" else 400
        return json_error(exc.code, exc.message, status)

    return json_success(
        {
            "lead": _serialize_lead_with_custom_fields(lead, g.organization_id),
            "action": action,
        },
        status=201 if action == "created" else 200,
    )


@api_bp.route("/leads/bulk", methods=["POST"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def bulk_leads():
    if not request.is_json:
        return json_error("validation_error", "JSON body is required.", 400)

    body = request.get_json(silent=True)
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict) and isinstance(body.get("leads"), list):
        items = body["leads"]
    else:
        return json_error("validation_error", "Body must be a JSON array or object with leads array.", 400)

    try:
        result = bulk_upsert_leads(g.organization_id, items)
    except ApiServiceError as exc:
        db.session.rollback()
        return json_error(exc.code, exc.message, 400)

    status = 200
    if result["errors"] and result["created"] == 0 and result["updated"] == 0:
        status = 400
    return json_success(result, status=status)


@api_bp.route("/leads", methods=["GET"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def list_leads():
    try:
        data = list_leads_api(g.organization_id, request.args)
    except ApiServiceError as exc:
        return json_error(exc.code, exc.message, 400)
    return json_success(data)


@api_bp.route("/leads/<int:lead_id>", methods=["GET"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def get_lead(lead_id: int):
    try:
        lead = get_lead_api(g.organization_id, lead_id)
    except ApiServiceError as exc:
        return json_error(exc.code, exc.message, 404)
    return json_success(
        {"lead": _serialize_lead_with_custom_fields(lead, g.organization_id)}
    )


@api_bp.route("/leads/<int:lead_id>/sequences/enroll", methods=["POST"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def enroll_lead_sequence(lead_id: int):
    if not request.is_json:
        return json_error("validation_error", "JSON body is required.", 400)

    body = request.get_json(silent=True) or {}
    sequence_id = body.get("sequence_id")
    if not sequence_id:
        return json_error("validation_error", "sequence_id is required.", 400)

    from app.sequences.services import SequenceService, SequenceServiceError

    try:
        enrollment = SequenceService.enroll_lead(
            lead_id,
            int(sequence_id),
            enrolled_by=None,
            organization_id=g.organization_id,
        )
        db.session.commit()
    except SequenceServiceError as exc:
        db.session.rollback()
        status = 404 if exc.code == "not_found" else 400
        return json_error(exc.code, exc.message, status)

    return json_success(
        {
            "enrollment": {
                "id": enrollment.id,
                "sequence_id": enrollment.sequence_id,
                "lead_id": enrollment.lead_id,
                "status": enrollment.status,
                "next_send_at": enrollment.next_send_at.isoformat()
                if enrollment.next_send_at
                else None,
            }
        },
        status=201,
    )


@api_bp.route("/leads/<int:lead_id>", methods=["PATCH"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def update_lead(lead_id: int):
    if not request.is_json:
        return json_error("validation_error", "JSON body is required.", 400)

    try:
        lead = patch_lead(g.organization_id, lead_id, request.get_json(silent=True))
        db.session.commit()
    except ApiServiceError as exc:
        db.session.rollback()
        status = 404 if exc.code == "not_found" else 400
        return json_error(exc.code, exc.message, status)

    return json_success(
        {"lead": _serialize_lead_with_custom_fields(lead, g.organization_id)}
    )


@api_bp.route("/leads/<int:lead_id>/tasks", methods=["POST"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def create_lead_task(lead_id: int):
    if not request.is_json:
        return json_error("validation_error", "JSON body is required.", 400)

    try:
        task = create_lead_task_api(g.organization_id, lead_id, request.get_json(silent=True))
        db.session.commit()
    except ApiServiceError as exc:
        db.session.rollback()
        status = 404 if exc.code == "not_found" else 400
        return json_error(exc.code, exc.message, status)

    return json_success({"task": task}, status=201)


@api_bp.route("/leads/<int:lead_id>/enrich", methods=["POST"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def enrich_lead(lead_id: int):
    try:
        enrich_lead_api(g.organization_id, lead_id)
        db.session.commit()
    except ApiServiceError as exc:
        db.session.rollback()
        if exc.code == "ai_disabled":
            return json_error("ai_disabled", exc.message, 400)
        status = 404 if exc.code == "not_found" else 400
        return json_error(exc.code, exc.message, status)

    return json_success({"status": "pending"})


@api_bp.route("/pipeline/stages", methods=["GET"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def pipeline_stages():
    stages = list_pipeline_stages(g.organization_id)
    return json_success({"stages": [serialize_stage(s) for s in stages]})


# --- Custom fields ---


@api_bp.route("/custom-fields", methods=["GET"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def list_custom_fields():
    entity_type = request.args.get("entity_type", "lead")
    try:
        fields = list_custom_field_definitions(g.organization_id, entity_type)
    except ApiServiceError as exc:
        return json_error(exc.code, exc.message, 400)
    return json_success(
        {"custom_fields": [serialize_custom_field_definition(f) for f in fields]}
    )


@api_bp.route("/custom-fields", methods=["POST"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def create_custom_field():
    if not request.is_json:
        return json_error("validation_error", "JSON body is required.", 400)
    try:
        field = create_custom_field_definition(g.organization_id, request.get_json(silent=True))
        db.session.commit()
    except ApiServiceError as exc:
        db.session.rollback()
        return json_error(exc.code, exc.message, 400)
    return json_success(
        {"custom_field": serialize_custom_field_definition(field)}, status=201
    )


@api_bp.route("/custom-fields/<int:field_id>", methods=["PATCH"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def patch_custom_field(field_id: int):
    if not request.is_json:
        return json_error("validation_error", "JSON body is required.", 400)
    try:
        field = update_custom_field_definition(
            g.organization_id, field_id, request.get_json(silent=True)
        )
        db.session.commit()
    except ApiServiceError as exc:
        db.session.rollback()
        status = 404 if exc.code == "not_found" else 400
        return json_error(exc.code, exc.message, status)
    return json_success({"custom_field": serialize_custom_field_definition(field)})


@api_bp.route("/custom-fields/<int:field_id>", methods=["DELETE"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def remove_custom_field(field_id: int):
    try:
        delete_custom_field_definition(g.organization_id, field_id)
        db.session.commit()
    except ApiServiceError as exc:
        db.session.rollback()
        status = 404 if exc.code == "not_found" else 400
        return json_error(exc.code, exc.message, status)
    return json_success({"deleted": True})


# --- Segments ---


@api_bp.route("/segments", methods=["GET"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def list_segments():
    segments = list_segments_api(g.organization_id)
    return json_success({"segments": [serialize_segment(s) for s in segments]})


@api_bp.route("/segments", methods=["POST"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def create_segment():
    if not request.is_json:
        return json_error("validation_error", "JSON body is required.", 400)
    try:
        segment = create_segment_api(g.organization_id, request.get_json(silent=True))
        db.session.commit()
    except ApiServiceError as exc:
        db.session.rollback()
        return json_error(exc.code, exc.message, 400)
    return json_success({"segment": serialize_segment(segment)}, status=201)


@api_bp.route("/segments/<int:segment_id>", methods=["GET"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def get_segment(segment_id: int):
    try:
        segment = get_segment_api(g.organization_id, segment_id)
    except ApiServiceError as exc:
        return json_error(exc.code, exc.message, 404)
    return json_success({"segment": serialize_segment(segment)})


@api_bp.route("/segments/<int:segment_id>", methods=["PATCH"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def patch_segment(segment_id: int):
    if not request.is_json:
        return json_error("validation_error", "JSON body is required.", 400)
    try:
        segment = update_segment_api(
            g.organization_id, segment_id, request.get_json(silent=True)
        )
        db.session.commit()
    except ApiServiceError as exc:
        db.session.rollback()
        status = 404 if exc.code == "not_found" else 400
        return json_error(exc.code, exc.message, status)
    return json_success({"segment": serialize_segment(segment)})


@api_bp.route("/segments/<int:segment_id>", methods=["DELETE"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def remove_segment(segment_id: int):
    try:
        delete_segment_api(g.organization_id, segment_id)
        db.session.commit()
    except ApiServiceError as exc:
        db.session.rollback()
        status = 404 if exc.code == "not_found" else 400
        return json_error(exc.code, exc.message, status)
    return json_success({"deleted": True})


@api_bp.route("/segments/<int:segment_id>/leads", methods=["GET"])
@require_api_key
@limiter.limit(api_rate_limit, key_func=api_rate_limit_key)
def segment_leads(segment_id: int):
    try:
        data = list_segment_leads_api(g.organization_id, segment_id, request.args)
    except ApiServiceError as exc:
        return json_error(exc.code, exc.message, 404 if exc.code == "not_found" else 400)
    return json_success(data)
