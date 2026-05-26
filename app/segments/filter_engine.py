"""Build SQLAlchemy lead queries from segment filter JSON (no Python-side full scans)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.orm import Query

from app.custom_fields.models import CustomFieldDefinition, CustomFieldValue
from app.leads.models import Lead, PipelineStage
from app.segments.relative_dates import resolve_filter_datetime

TEXT_OPERATORS = frozenset({"eq", "neq", "contains", "not_contains", "is_empty", "is_not_empty"})
NUMBER_OPERATORS = frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"})
DATE_OPERATORS = frozenset({"eq", "before", "after", "between", "is_empty", "is_not_empty"})
BOOLEAN_OPERATORS = frozenset({"is_true", "is_false"})
SELECT_OPERATORS = frozenset({"eq", "neq", "in", "not_in"})
STAGE_OPERATORS = frozenset({"eq", "neq", "in", "not_in"})

STANDARD_FIELDS = {
    "score": "number",
    "source": "text",
    "status": "text",
    "email": "text",
    "company": "text",
    "first_name": "text",
    "last_name": "text",
    "phone": "text",
    "title": "text",
    "assigned_to": "number",
    "created_at": "date",
    "updated_at": "date",
    "last_contacted_at": "date",
    "stage.name": "stage",
    "stage_id": "number",
    "tags": "text",
}


class FilterEngineError(Exception):
    def __init__(self, message: str, code: str = "invalid_filter"):
        self.message = message
        self.code = code
        super().__init__(message)


def apply_segment_filters(
    query: Query,
    organization_id: int,
    filters: dict | None,
    *,
    custom_field_cache: dict[str, CustomFieldDefinition] | None = None,
) -> Query:
    """Apply segment filter tree to an existing Lead query."""
    query = query.filter(Lead.organization_id == organization_id)
    if not filters:
        return query.filter(Lead.status != "archived")

    root = filters
    if "conditions" in root and "logic" not in root:
        root = {"logic": "AND", "rules": [{"type": "condition", **c} for c in root["conditions"]]}

    expr = _build_group(root, organization_id, custom_field_cache or {})
    if expr is not None:
        query = query.filter(expr)
    return query


def _build_group(
    node: dict,
    organization_id: int,
    custom_fields: dict[str, CustomFieldDefinition],
) -> Any:
    logic = (node.get("logic") or "AND").upper()
    if logic not in ("AND", "OR"):
        raise FilterEngineError("Filter logic must be AND or OR.")

    rules = node.get("rules")
    if rules is None:
        rules = node.get("conditions") or []

    parts = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise FilterEngineError("Invalid filter rule.")
        if rule.get("type") == "group" or "rules" in rule or (
            "logic" in rule and ("conditions" in rule or "rules" in rule)
        ):
            group_node = rule
            if "type" in group_node and group_node["type"] == "condition":
                parts.append(_build_condition(rule, organization_id, custom_fields))
            else:
                parts.append(_build_group(group_node, organization_id, custom_fields))
        else:
            cond = rule
            if cond.get("type") == "group":
                parts.append(_build_group(cond, organization_id, custom_fields))
            else:
                parts.append(_build_condition(cond, organization_id, custom_fields))

    parts = [p for p in parts if p is not None]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return and_(*parts) if logic == "AND" else or_(*parts)


def _build_condition(
    cond: dict,
    organization_id: int,
    custom_fields: dict[str, CustomFieldDefinition],
) -> Any:
    field = (cond.get("field") or "").strip()
    operator = (cond.get("operator") or "").strip()
    value = cond.get("value")

    if not field or not operator:
        raise FilterEngineError("Filter condition requires field and operator.")

    if field.startswith("custom."):
        name = field.split(".", 1)[1]
        defn = custom_fields.get(name)
        if not defn:
            defn = CustomFieldDefinition.query.filter_by(
                organization_id=organization_id,
                entity_type="lead",
                name=name,
            ).first()
            if defn:
                custom_fields[name] = defn
        if not defn:
            raise FilterEngineError(f"Unknown custom field: {name}")
        return _custom_field_condition(defn, operator, value, organization_id)

    field_type = STANDARD_FIELDS.get(field)
    if not field_type:
        raise FilterEngineError(f"Unknown filter field: {field}")

    if field_type == "stage":
        return _stage_condition(operator, value, organization_id)
    if field_type == "number":
        return _number_condition(_lead_column(field), operator, value)
    if field_type == "date":
        return _date_condition(_lead_column(field), operator, value)
    if field == "tags":
        return _tags_condition(operator, value)
    return _text_condition(_lead_column(field), operator, value)


def _lead_column(field: str):
    mapping = {
        "stage_id": Lead.stage_id,
        "assigned_to": Lead.assigned_to,
    }
    if field in mapping:
        return mapping[field]
    return getattr(Lead, field)


def _text_condition(column, operator: str, value) -> Any:
    if operator not in TEXT_OPERATORS:
        raise FilterEngineError(f"Invalid operator for text field: {operator}")
    if operator == "is_empty":
        return or_(column.is_(None), column == "")
    if operator == "is_not_empty":
        return and_(column.isnot(None), column != "")
    if value is None:
        raise FilterEngineError("Filter value is required.")
    text = str(value)
    if operator == "eq":
        return column == text
    if operator == "neq":
        return column != text
    pattern = f"%{text}%"
    if operator == "contains":
        return column.ilike(pattern)
    if operator == "not_contains":
        return not_(column.ilike(pattern))
    raise FilterEngineError(f"Unsupported text operator: {operator}")


def _number_condition(column, operator: str, value) -> Any:
    if operator not in NUMBER_OPERATORS:
        raise FilterEngineError(f"Invalid operator for number field: {operator}")
    if operator == "is_empty":
        return column.is_(None)
    if operator == "is_not_empty":
        return column.isnot(None)
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise FilterEngineError("Number filter value must be numeric.") from None
    ops = {
        "eq": column == num,
        "neq": column != num,
        "gt": column > num,
        "gte": column >= num,
        "lt": column < num,
        "lte": column <= num,
    }
    return ops[operator]


def _date_condition(column, operator: str, value) -> Any:
    if operator not in DATE_OPERATORS:
        raise FilterEngineError(f"Invalid operator for date field: {operator}")
    if operator == "is_empty":
        return column.is_(None)
    if operator == "is_not_empty":
        return column.isnot(None)
    if operator == "between":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise FilterEngineError("Between operator requires [start, end].")
        start = resolve_filter_datetime(value[0])
        end = resolve_filter_datetime(value[1])
        return and_(column >= start, column <= end)
    resolved = resolve_filter_datetime(value)
    if operator == "eq":
        return column == resolved
    if operator == "before":
        return column < resolved
    if operator == "after":
        return column > resolved
    raise FilterEngineError(f"Unsupported date operator: {operator}")


def _tags_condition(operator: str, value) -> Any:
    if operator not in SELECT_OPERATORS | {"contains", "is_empty", "is_not_empty"}:
        raise FilterEngineError(f"Invalid operator for tags: {operator}")
    if operator == "is_empty":
        return or_(Lead.tags.is_(None), Lead.tags == [])
    if operator == "is_not_empty":
        return and_(Lead.tags.isnot(None), Lead.tags != [])
    if operator in ("eq", "contains"):
        tag = str(value)
        return Lead.tags.contains([tag])
    if operator == "neq":
        return not_(Lead.tags.contains([str(value)]))
    if operator == "in":
        if not isinstance(value, list):
            raise FilterEngineError("In operator requires a list.")
        return or_(*[Lead.tags.contains([str(v)]) for v in value])
    if operator == "not_in":
        if not isinstance(value, list):
            raise FilterEngineError("Not in operator requires a list.")
        return and_(*[not_(Lead.tags.contains([str(v)])) for v in value])
    raise FilterEngineError(f"Unsupported tags operator: {operator}")


def _stage_condition(operator: str, value, organization_id: int) -> Any:
    if operator not in STAGE_OPERATORS:
        raise FilterEngineError(f"Invalid operator for stage: {operator}")

    subq = (
        select(PipelineStage.id)
        .where(
            PipelineStage.organization_id == organization_id,
        )
    )

    if operator == "eq":
        subq = subq.where(PipelineStage.name == str(value))
    elif operator == "neq":
        return Lead.stage_id.notin_(
            select(PipelineStage.id).where(
                PipelineStage.organization_id == organization_id,
                PipelineStage.name == str(value),
            )
        )
    elif operator == "in":
        if not isinstance(value, list):
            raise FilterEngineError("In operator requires a list.")
        subq = subq.where(PipelineStage.name.in_([str(v) for v in value]))
    elif operator == "not_in":
        if not isinstance(value, list):
            raise FilterEngineError("Not in operator requires a list.")
        return Lead.stage_id.notin_(
            select(PipelineStage.id).where(
                PipelineStage.organization_id == organization_id,
                PipelineStage.name.in_([str(v) for v in value]),
            )
        )
    else:
        raise FilterEngineError(f"Unsupported stage operator: {operator}")

    return Lead.stage_id.in_(subq)


def _custom_field_condition(
    defn: CustomFieldDefinition,
    operator: str,
    value,
    organization_id: int,
) -> Any:
    ft = defn.field_type

    if ft in ("text", "url"):
        return _custom_text_exists(defn, organization_id, operator, value)
    if ft == "number":
        return _custom_number_exists(defn, organization_id, operator, value)
    if ft == "date":
        return _custom_date_exists(defn, organization_id, operator, value)
    if ft == "boolean":
        return _custom_boolean_exists(defn, organization_id, operator)
    if ft in ("select", "multiselect"):
        return _custom_select_exists(defn, organization_id, operator, value)
    raise FilterEngineError(f"Cannot filter on field type: {ft}")


def _base_value_exists(defn_id: int, organization_id: int) -> Any:
    return exists(
        select(CustomFieldValue.id).where(
            CustomFieldValue.organization_id == organization_id,
            CustomFieldValue.entity_type == "lead",
            CustomFieldValue.entity_id == Lead.id,
            CustomFieldValue.field_definition_id == defn_id,
        )
    )


def _custom_text_exists(defn, organization_id, operator, value) -> Any:
    col = CustomFieldValue.value_text
    if operator not in TEXT_OPERATORS:
        raise FilterEngineError(f"Invalid operator for custom text: {operator}")

    if operator == "is_empty":
        return not_(_value_matches(defn.id, organization_id, col.isnot(None), col != ""))
    if operator == "is_not_empty":
        return _value_matches(defn.id, organization_id, col.isnot(None), col != "")

    return _value_matches(defn.id, organization_id, _text_sql(col, operator, value))


def _text_sql(column, operator, value):
    text = str(value)
    if operator == "eq":
        return column == text
    if operator == "neq":
        return column != text
    pattern = f"%{text}%"
    if operator == "contains":
        return column.ilike(pattern)
    if operator == "not_contains":
        return not_(column.ilike(pattern))
    raise FilterEngineError(f"Unsupported operator: {operator}")


def _value_matches(defn_id, organization_id, *criteria) -> Any:
    return exists(
        select(CustomFieldValue.id).where(
            CustomFieldValue.organization_id == organization_id,
            CustomFieldValue.entity_type == "lead",
            CustomFieldValue.entity_id == Lead.id,
            CustomFieldValue.field_definition_id == defn_id,
            *criteria,
        )
    )


def _custom_number_exists(defn, organization_id, operator, value) -> Any:
    col = CustomFieldValue.value_number
    if operator == "is_empty":
        return not_(_value_matches(defn.id, organization_id, col.isnot(None)))
    if operator == "is_not_empty":
        return _value_matches(defn.id, organization_id, col.isnot(None))
    num = float(value)
    ops = {
        "eq": col == num,
        "neq": col != num,
        "gt": col > num,
        "gte": col >= num,
        "lt": col < num,
        "lte": col <= num,
    }
    if operator not in ops:
        raise FilterEngineError(f"Invalid number operator: {operator}")
    return _value_matches(defn.id, organization_id, ops[operator])


def _custom_date_exists(defn, organization_id, operator, value) -> Any:
    col = CustomFieldValue.value_date
    if operator == "is_empty":
        return not_(_value_matches(defn.id, organization_id, col.isnot(None)))
    if operator == "is_not_empty":
        return _value_matches(defn.id, organization_id, col.isnot(None))
    if operator == "between":
        start = resolve_filter_datetime(value[0])
        end = resolve_filter_datetime(value[1])
        return _value_matches(defn.id, organization_id, col >= start, col <= end)
    resolved = resolve_filter_datetime(value)
    ops = {"eq": col == resolved, "before": col < resolved, "after": col > resolved}
    if operator not in ops:
        raise FilterEngineError(f"Invalid date operator: {operator}")
    return _value_matches(defn.id, organization_id, ops[operator])


def _custom_boolean_exists(defn, organization_id, operator) -> Any:
    if operator not in BOOLEAN_OPERATORS:
        raise FilterEngineError(f"Invalid boolean operator: {operator}")
    val = operator == "is_true"
    return _value_matches(
        defn.id, organization_id, CustomFieldValue.value_boolean.is_(val)
    )


def _custom_select_exists(defn, organization_id, operator, value) -> Any:
    col = CustomFieldValue.value_text
    if defn.field_type == "multiselect":
        return _custom_multiselect_exists(defn, organization_id, operator, value)

    if operator not in SELECT_OPERATORS | TEXT_OPERATORS:
        raise FilterEngineError(f"Invalid select operator: {operator}")
    if operator == "is_empty":
        return not_(_value_matches(defn.id, organization_id, col.isnot(None), col != ""))
    if operator == "is_not_empty":
        return _value_matches(defn.id, organization_id, col.isnot(None), col != "")
    if operator == "eq":
        return _value_matches(defn.id, organization_id, col == str(value))
    if operator == "neq":
        return not_(_value_matches(defn.id, organization_id, col == str(value)))
    if operator == "in":
        return _value_matches(defn.id, organization_id, col.in_([str(v) for v in value]))
    if operator == "not_in":
        return not_(_value_matches(defn.id, organization_id, col.in_([str(v) for v in value])))
    raise FilterEngineError(f"Unsupported select operator: {operator}")


def _custom_multiselect_exists(defn, organization_id, operator, value) -> Any:
    if operator == "is_empty":
        return not_(_value_matches(defn.id, organization_id, CustomFieldValue.value_json.isnot(None)))
    if operator == "is_not_empty":
        return _value_matches(defn.id, organization_id, CustomFieldValue.value_json.isnot(None))
    if operator == "eq":
        return _value_matches(
            defn.id,
            organization_id,
            CustomFieldValue.value_json.contains([str(value)]),
        )
    if operator == "in":
        return or_(
            *[
                _value_matches(
                    defn.id,
                    organization_id,
                    CustomFieldValue.value_json.contains([str(v)]),
                )
                for v in value
            ]
        )
    raise FilterEngineError(f"Unsupported multiselect operator: {operator}")
