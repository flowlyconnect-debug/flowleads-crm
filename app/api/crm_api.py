"""API wrappers for custom fields and segments."""

from __future__ import annotations

from app.api.services import ApiServiceError
from app.custom_fields.models import CustomFieldDefinition
from app.custom_fields.services import CustomFieldService, CustomFieldServiceError
from app.segments.models import Segment
from app.segments.services import SegmentService, SegmentServiceError


def _map_custom_field_error(exc: CustomFieldServiceError) -> ApiServiceError:
    status_code = exc.code
    return ApiServiceError(exc.message, status_code)


def _map_segment_error(exc: SegmentServiceError) -> ApiServiceError:
    return ApiServiceError(exc.message, exc.code)


# --- Custom field definitions ---


def list_custom_field_definitions(organization_id: int, entity_type: str = "lead"):
    try:
        return CustomFieldService.get_fields(organization_id, entity_type)
    except CustomFieldServiceError as exc:
        raise _map_custom_field_error(exc) from exc


def create_custom_field_definition(organization_id: int, payload: dict) -> CustomFieldDefinition:
    if not isinstance(payload, dict):
        raise ApiServiceError("Request body must be a JSON object.", "validation_error")
    try:
        return CustomFieldService.create_definition(organization_id, payload)
    except CustomFieldServiceError as exc:
        raise _map_custom_field_error(exc) from exc


def update_custom_field_definition(
    organization_id: int, field_id: int, payload: dict
) -> CustomFieldDefinition:
    if not isinstance(payload, dict):
        raise ApiServiceError("Request body must be a JSON object.", "validation_error")
    try:
        return CustomFieldService.update_definition(field_id, organization_id, payload)
    except CustomFieldServiceError as exc:
        raise _map_custom_field_error(exc) from exc


def delete_custom_field_definition(organization_id: int, field_id: int) -> None:
    try:
        CustomFieldService.delete_definition(field_id, organization_id)
    except CustomFieldServiceError as exc:
        raise _map_custom_field_error(exc) from exc


def set_lead_custom_fields(
    organization_id: int, lead_id: int, values: dict, *, partial: bool = True
) -> dict:
    if not isinstance(values, dict):
        raise ApiServiceError("custom_fields must be a JSON object.", "validation_error")
    try:
        return CustomFieldService.set_values_by_name(
            lead_id, "lead", values, organization_id, partial=partial
        )
    except CustomFieldServiceError as exc:
        raise _map_custom_field_error(exc) from exc


# --- Segments ---


def list_segments_api(organization_id: int) -> list[Segment]:
    return SegmentService.list_segments(organization_id)


def create_segment_api(organization_id: int, payload: dict) -> Segment:
    if not isinstance(payload, dict):
        raise ApiServiceError("Request body must be a JSON object.", "validation_error")
    try:
        return SegmentService.save(
            payload.get("name", ""),
            payload.get("filters") or {},
            organization_id,
            description=payload.get("description"),
            is_pinned=bool(payload.get("is_pinned", False)),
        )
    except SegmentServiceError as exc:
        raise _map_segment_error(exc) from exc


def get_segment_api(organization_id: int, segment_id: int) -> Segment:
    try:
        return SegmentService.get_segment(segment_id, organization_id)
    except SegmentServiceError as exc:
        raise _map_segment_error(exc) from exc


def update_segment_api(organization_id: int, segment_id: int, payload: dict) -> Segment:
    if not isinstance(payload, dict):
        raise ApiServiceError("Request body must be a JSON object.", "validation_error")
    try:
        return SegmentService.update(segment_id, organization_id, payload)
    except SegmentServiceError as exc:
        raise _map_segment_error(exc) from exc


def delete_segment_api(organization_id: int, segment_id: int) -> None:
    try:
        SegmentService.delete(segment_id, organization_id)
    except SegmentServiceError as exc:
        raise _map_segment_error(exc) from exc


def list_segment_leads_api(
    organization_id: int,
    segment_id: int,
    query_args: dict,
) -> dict:
    try:
        segment = SegmentService.get_segment(segment_id, organization_id)
    except SegmentServiceError as exc:
        raise _map_segment_error(exc) from exc

    page = max(1, int(query_args.get("page", 1) or 1))
    per_page = max(1, min(100, int(query_args.get("per_page", 25) or 25)))

    from app.api.schemas import serialize_lead
    from app.custom_fields.services import CustomFieldService

    try:
        pagination = SegmentService.paginate_leads(
            organization_id,
            segment.filters,
            page=page,
            per_page=per_page,
        )
    except SegmentServiceError as exc:
        raise _map_segment_error(exc) from exc

    lead_ids = [lead.id for lead in pagination.items]
    custom_by_lead = CustomFieldService.get_values_bulk(lead_ids, "lead", organization_id)

    leads = [
        serialize_lead(lead, custom_fields=custom_by_lead.get(lead.id, {}))
        for lead in pagination.items
    ]
    return {
        "segment": {
            "id": segment.id,
            "name": segment.name,
            "lead_count": segment.lead_count_cache,
        },
        "leads": leads,
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages,
        },
    }
