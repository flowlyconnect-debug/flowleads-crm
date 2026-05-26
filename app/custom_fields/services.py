from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import joinedload

from app.custom_fields.models import (
    ENTITY_TYPES,
    FIELD_TYPES,
    CustomFieldDefinition,
    CustomFieldValue,
)
from app.extensions import db
from app.leads.validators import validate_url_field


class CustomFieldServiceError(Exception):
    def __init__(self, message: str, code: str = "custom_field_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _normalize_name(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "_")


def _field_options_list(defn: CustomFieldDefinition) -> list[str]:
    raw = defn.options or []
    if isinstance(raw, dict):
        raw = raw.get("choices") or raw.get("options") or []
    if not isinstance(raw, list):
        return []
    return [str(o) for o in raw]


def _field_numeric_bounds(defn: CustomFieldDefinition) -> tuple[float | None, float | None]:
    raw = defn.options if isinstance(defn.options, dict) else {}
    min_val = raw.get("min") if isinstance(raw, dict) else None
    max_val = raw.get("max") if isinstance(raw, dict) else None
    return min_val, max_val


class CustomFieldService:
    @staticmethod
    def get_fields(organization_id: int, entity_type: str = "lead") -> list[CustomFieldDefinition]:
        if entity_type not in ENTITY_TYPES:
            raise CustomFieldServiceError("Invalid entity type.", "invalid_entity_type")
        return (
            CustomFieldDefinition.query.filter_by(
                organization_id=organization_id,
                entity_type=entity_type,
            )
            .order_by(CustomFieldDefinition.order_index.asc(), CustomFieldDefinition.id.asc())
            .all()
        )

    @staticmethod
    def get_definition(
        field_id: int, organization_id: int, entity_type: str | None = None
    ) -> CustomFieldDefinition:
        query = CustomFieldDefinition.query.filter_by(
            id=field_id, organization_id=organization_id
        )
        if entity_type:
            query = query.filter_by(entity_type=entity_type)
        defn = query.first()
        if not defn:
            raise CustomFieldServiceError("Custom field not found.", "not_found")
        return defn

    @staticmethod
    def get_definition_by_name(
        name: str, organization_id: int, entity_type: str = "lead"
    ) -> CustomFieldDefinition | None:
        normalized = _normalize_name(name)
        return CustomFieldDefinition.query.filter_by(
            organization_id=organization_id,
            entity_type=entity_type,
            name=normalized,
        ).first()

    @staticmethod
    def create_definition(organization_id: int, data: dict) -> CustomFieldDefinition:
        entity_type = (data.get("entity_type") or "lead").strip()
        if entity_type not in ENTITY_TYPES:
            raise CustomFieldServiceError("Invalid entity type.", "invalid_entity_type")

        field_type = (data.get("field_type") or "").strip()
        if field_type not in FIELD_TYPES:
            raise CustomFieldServiceError("Invalid field type.", "invalid_field_type")

        name = _normalize_name(data.get("name") or data.get("label") or "")
        if not name:
            raise CustomFieldServiceError("Field name is required.", "validation_error")
        if len(name) > 100:
            raise CustomFieldServiceError("Field name is too long.", "validation_error")

        label = (data.get("label") or name).strip()
        if not label:
            raise CustomFieldServiceError("Field label is required.", "validation_error")

        existing = CustomFieldService.get_definition_by_name(
            name, organization_id, entity_type
        )
        if existing:
            raise CustomFieldServiceError(
                "A custom field with this name already exists.", "duplicate_name"
            )

        options = data.get("options")
        if field_type in ("select", "multiselect"):
            choices = _field_options_list(
                CustomFieldDefinition(field_type=field_type, options=options)
            )
            if not choices:
                raise CustomFieldServiceError(
                    "Select fields require at least one option.", "validation_error"
                )

        defn = CustomFieldDefinition(
            organization_id=organization_id,
            entity_type=entity_type,
            name=name,
            label=label,
            field_type=field_type,
            options=options,
            is_required=bool(data.get("is_required", False)),
            is_searchable=bool(data.get("is_searchable", True)),
            order_index=int(data.get("order_index", 0)),
        )
        db.session.add(defn)
        db.session.flush()
        return defn

    @staticmethod
    def update_definition(
        field_id: int, organization_id: int, data: dict
    ) -> CustomFieldDefinition:
        defn = CustomFieldService.get_definition(field_id, organization_id)

        if "label" in data and data["label"]:
            defn.label = str(data["label"]).strip()
        if "options" in data:
            defn.options = data["options"]
            if defn.field_type in ("select", "multiselect") and not _field_options_list(defn):
                raise CustomFieldServiceError(
                    "Select fields require at least one option.", "validation_error"
                )
        if "is_required" in data:
            defn.is_required = bool(data["is_required"])
        if "is_searchable" in data:
            defn.is_searchable = bool(data["is_searchable"])
        if "order_index" in data:
            defn.order_index = int(data["order_index"])

        db.session.flush()
        return defn

    @staticmethod
    def delete_definition(field_id: int, organization_id: int) -> None:
        defn = CustomFieldService.get_definition(field_id, organization_id)
        CustomFieldValue.query.filter_by(
            organization_id=organization_id,
            field_definition_id=defn.id,
        ).delete(synchronize_session=False)
        db.session.delete(defn)
        db.session.flush()

    @staticmethod
    def validate_value(defn: CustomFieldDefinition, raw_value: Any) -> dict:
        """Validate and return typed storage dict for a custom field value."""
        field_type = defn.field_type

        if raw_value is None or raw_value == "":
            if defn.is_required:
                raise CustomFieldServiceError(
                    f"{defn.label} is required.", "validation_error"
                )
            return {
                "value_text": None,
                "value_number": None,
                "value_date": None,
                "value_boolean": None,
                "value_json": None,
            }

        if field_type == "text":
            text = str(raw_value).strip()
            if len(text) > 1000:
                raise CustomFieldServiceError(
                    f"{defn.label} must be at most 1000 characters.", "validation_error"
                )
            return {"value_text": text, "value_number": None, "value_date": None,
                    "value_boolean": None, "value_json": None}

        if field_type == "number":
            try:
                num = float(raw_value)
            except (TypeError, ValueError):
                raise CustomFieldServiceError(
                    f"{defn.label} must be a number.", "validation_error"
                ) from None
            min_val, max_val = _field_numeric_bounds(defn)
            if min_val is not None and num < float(min_val):
                raise CustomFieldServiceError(
                    f"{defn.label} must be at least {min_val}.", "validation_error"
                )
            if max_val is not None and num > float(max_val):
                raise CustomFieldServiceError(
                    f"{defn.label} must be at most {max_val}.", "validation_error"
                )
            return {"value_text": None, "value_number": num, "value_date": None,
                    "value_boolean": None, "value_json": None}

        if field_type == "date":
            parsed = _parse_date_value(raw_value)
            return {"value_text": None, "value_number": None, "value_date": parsed,
                    "value_boolean": None, "value_json": None}

        if field_type == "boolean":
            if isinstance(raw_value, bool):
                bool_val = raw_value
            elif str(raw_value).lower() in ("true", "1", "yes"):
                bool_val = True
            elif str(raw_value).lower() in ("false", "0", "no"):
                bool_val = False
            else:
                raise CustomFieldServiceError(
                    f"{defn.label} must be true or false.", "validation_error"
                )
            return {"value_text": None, "value_number": None, "value_date": None,
                    "value_boolean": bool_val, "value_json": None}

        if field_type == "select":
            text = str(raw_value).strip()
            choices = _field_options_list(defn)
            if text not in choices:
                raise CustomFieldServiceError(
                    f"{defn.label}: invalid option.", "invalid_option"
                )
            return {"value_text": text, "value_number": None, "value_date": None,
                    "value_boolean": None, "value_json": None}

        if field_type == "multiselect":
            if not isinstance(raw_value, list):
                raise CustomFieldServiceError(
                    f"{defn.label} must be a list.", "validation_error"
                )
            choices = _field_options_list(defn)
            normalized = [str(v).strip() for v in raw_value if str(v).strip()]
            invalid = [v for v in normalized if v not in choices]
            if invalid:
                raise CustomFieldServiceError(
                    f"{defn.label}: invalid option(s).", "invalid_option"
                )
            return {"value_text": None, "value_number": None, "value_date": None,
                    "value_boolean": None, "value_json": normalized}

        if field_type == "url":
            text = str(raw_value).strip()
            if not validate_url_field(text):
                raise CustomFieldServiceError(
                    f"{defn.label}: invalid URL.", "invalid_url"
                )
            return {"value_text": text, "value_number": None, "value_date": None,
                    "value_boolean": None, "value_json": None}

        raise CustomFieldServiceError("Unsupported field type.", "invalid_field_type")

    @staticmethod
    def value_to_api(defn: CustomFieldDefinition, row: CustomFieldValue | None) -> Any:
        if row is None:
            return None
        ft = defn.field_type
        if ft == "number":
            return row.value_number
        if ft == "date" and row.value_date:
            return row.value_date.isoformat()
        if ft == "boolean":
            return row.value_boolean
        if ft == "multiselect":
            return list(row.value_json or [])
        if ft in ("text", "select", "url"):
            return row.value_text
        return None

    @staticmethod
    def get_values(
        entity_id: int, entity_type: str, organization_id: int
    ) -> dict[str, Any]:
        """Return {field_name: api_value} for one entity."""
        definitions = {
            d.id: d
            for d in CustomFieldService.get_fields(organization_id, entity_type)
        }
        if not definitions:
            return {}

        rows = CustomFieldValue.query.filter_by(
            organization_id=organization_id,
            entity_id=entity_id,
            entity_type=entity_type,
        ).filter(CustomFieldValue.field_definition_id.in_(definitions.keys())).all()

        result: dict[str, Any] = {}
        for row in rows:
            defn = definitions.get(row.field_definition_id)
            if defn:
                result[defn.name] = CustomFieldService.value_to_api(defn, row)
        return result

    @staticmethod
    def get_values_bulk(
        entity_ids: list[int], entity_type: str, organization_id: int
    ) -> dict[int, dict[str, Any]]:
        """Batch-load custom fields for many entities (avoids N+1)."""
        if not entity_ids:
            return {}

        definitions = CustomFieldService.get_fields(organization_id, entity_type)
        if not definitions:
            return {eid: {} for eid in entity_ids}

        def_by_id = {d.id: d for d in definitions}
        rows = (
            CustomFieldValue.query.filter(
                CustomFieldValue.organization_id == organization_id,
                CustomFieldValue.entity_type == entity_type,
                CustomFieldValue.entity_id.in_(entity_ids),
            )
            .options(joinedload(CustomFieldValue.definition))
            .all()
        )

        result: dict[int, dict[str, Any]] = {eid: {} for eid in entity_ids}
        for row in rows:
            defn = def_by_id.get(row.field_definition_id)
            if not defn:
                continue
            result.setdefault(row.entity_id, {})[defn.name] = CustomFieldService.value_to_api(
                defn, row
            )
        return result

    @staticmethod
    def set_value(
        entity_id: int,
        entity_type: str,
        field_id: int,
        raw_value: Any,
        organization_id: int,
    ) -> CustomFieldValue | None:
        defn = CustomFieldService.get_definition(field_id, organization_id, entity_type)
        typed = CustomFieldService.validate_value(defn, raw_value)

        if all(v is None for v in typed.values()):
            CustomFieldValue.query.filter_by(
                organization_id=organization_id,
                entity_id=entity_id,
                entity_type=entity_type,
                field_definition_id=defn.id,
            ).delete(synchronize_session=False)
            db.session.flush()
            return None

        row = CustomFieldValue.query.filter_by(
            organization_id=organization_id,
            entity_id=entity_id,
            entity_type=entity_type,
            field_definition_id=defn.id,
        ).first()

        if not row:
            row = CustomFieldValue(
                organization_id=organization_id,
                entity_id=entity_id,
                entity_type=entity_type,
                field_definition_id=defn.id,
            )
            db.session.add(row)

        for key, val in typed.items():
            setattr(row, key, val)
        row.updated_at = datetime.now(timezone.utc)
        db.session.flush()
        return row

    @staticmethod
    def set_values_by_name(
        entity_id: int,
        entity_type: str,
        values: dict[str, Any],
        organization_id: int,
        *,
        partial: bool = False,
    ) -> dict[str, Any]:
        if not values:
            return {}

        definitions = {
            d.name: d for d in CustomFieldService.get_fields(organization_id, entity_type)
        }
        saved: dict[str, Any] = {}

        for name, raw in values.items():
            normalized = _normalize_name(name)
            defn = definitions.get(normalized)
            if not defn:
                if partial:
                    continue
                raise CustomFieldServiceError(
                    f"Unknown custom field: {name}", "unknown_field"
                )
            CustomFieldService.set_value(
                entity_id, entity_type, defn.id, raw, organization_id
            )
            row = CustomFieldValue.query.filter_by(
                organization_id=organization_id,
                entity_id=entity_id,
                entity_type=entity_type,
                field_definition_id=defn.id,
            ).first()
            saved[defn.name] = CustomFieldService.value_to_api(defn, row)
        return saved

    @staticmethod
    def validate_required_for_entity(
        entity_id: int, entity_type: str, organization_id: int, payload: dict | None = None
    ) -> None:
        payload = payload or {}
        for defn in CustomFieldService.get_fields(organization_id, entity_type):
            if not defn.is_required:
                continue
            if defn.name in payload:
                CustomFieldService.validate_value(defn, payload[defn.name])
                continue
            row = CustomFieldValue.query.filter_by(
                organization_id=organization_id,
                entity_id=entity_id,
                entity_type=entity_type,
                field_definition_id=defn.id,
            ).first()
            if row is None or CustomFieldService.value_to_api(defn, row) in (None, [], ""):
                raise CustomFieldServiceError(
                    f"{defn.label} is required.", "validation_error"
                )


def _parse_date_value(raw_value: Any) -> datetime:
    if isinstance(raw_value, datetime):
        if raw_value.tzinfo is None:
            return raw_value.replace(tzinfo=timezone.utc)
        return raw_value
    if isinstance(raw_value, date):
        return datetime.combine(raw_value, datetime.min.time(), tzinfo=timezone.utc)
    text = str(raw_value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CustomFieldServiceError("Invalid date format.", "validation_error") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
