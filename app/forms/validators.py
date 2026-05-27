from __future__ import annotations

import re

from app.core.security import validate_email
from app.forms.models import WEB_FORM_FIELD_TYPES

_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def validate_fields_config(fields) -> tuple[bool, str | None]:
    if not isinstance(fields, list):
        return False, "Fields must be a JSON array."
    if not fields:
        return False, "At least one field is required."

    seen_keys: set[str] = set()
    for idx, field in enumerate(fields):
        if not isinstance(field, dict):
            return False, f"Field at index {idx} must be an object."

        key = field.get("key")
        if not key or not isinstance(key, str):
            return False, f"Field at index {idx} requires a key."
        key = key.strip()
        if not _FIELD_KEY_RE.match(key):
            return False, f"Invalid field key: {key!r}."
        if key in seen_keys:
            return False, f"Duplicate field key: {key!r}."
        seen_keys.add(key)

        label = field.get("label")
        if not label or not str(label).strip():
            return False, f"Field {key!r} requires a label."

        field_type = field.get("type")
        if field_type not in WEB_FORM_FIELD_TYPES:
            return False, f"Invalid field type for {key!r}."

        if field.get("required") is not None and not isinstance(field.get("required"), bool):
            return False, f"Field {key!r} required must be a boolean."

        if field_type == "select":
            options = field.get("options")
            if not isinstance(options, list) or not options:
                return False, f"Select field {key!r} requires options."
            for opt in options:
                if not isinstance(opt, str) or not str(opt).strip():
                    return False, f"Select field {key!r} has invalid option."

    return True, None


def validate_submission_payload(
    fields_config: list,
    payload: dict,
) -> tuple[dict | None, dict | None]:
    """Validate submission against form config. Returns (normalized_data, error_dict)."""
    if not isinstance(payload, dict):
        return None, {
            "code": "validation_error",
            "message": "Invalid submission payload.",
        }

    normalized: dict = {}
    errors: dict[str, str] = {}

    for field in fields_config:
        key = field["key"]
        field_type = field["type"]
        required = bool(field.get("required"))
        raw = payload.get(key)

        if field_type == "checkbox":
            if raw is None or raw == "":
                value = False
            elif isinstance(raw, bool):
                value = raw
            elif str(raw).lower() in ("true", "1", "on", "yes"):
                value = True
            elif str(raw).lower() in ("false", "0", "off", "no"):
                value = False
            else:
                errors[key] = "Invalid checkbox value."
                continue
        else:
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                if required:
                    errors[key] = "This field is required."
                continue
            value = str(raw).strip() if not isinstance(raw, (int, float)) else raw

        if field_type == "email" and value:
            if not validate_email(str(value)):
                errors[key] = "Invalid email address."
                continue

        if field_type == "select" and value:
            options = field.get("options") or []
            if str(value) not in options:
                errors[key] = "Invalid selection."
                continue

        if field_type == "number" and value != "":
            try:
                float(value)
            except (TypeError, ValueError):
                errors[key] = "Must be a number."
                continue

        normalized[key] = value

    if errors:
        return None, {
            "code": "validation_error",
            "message": "Please correct the highlighted fields.",
            "fields": errors,
        }

    return normalized, None
