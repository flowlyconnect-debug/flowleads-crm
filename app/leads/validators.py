import re

from app.core.security import normalize_email, validate_email
from app.leads.models import LEAD_SOURCES, LEAD_STATUSES

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
URL_RE = re.compile(
    r"^https?://[^\s/$.?#].[^\s]*$|^www\.[^\s/$.?#].[^\s]*$",
    re.IGNORECASE,
)


def validate_hex_color(color: str | None) -> bool:
    return bool(color and HEX_COLOR_RE.match(color))


def validate_url_field(url: str | None) -> bool:
    if not url or not url.strip():
        return True
    value = url.strip()
    if len(value) > 500:
        return False
    if not value.startswith(("http://", "https://", "www.")):
        value = f"https://{value}"
    return bool(URL_RE.match(value))


def normalize_tags(tags) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        if not tags.strip():
            return []
        return [t.strip() for t in tags.split(",") if t.strip()]
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    return []


def validate_tags(tags) -> tuple[bool, str | None]:
    normalized = normalize_tags(tags)
    for tag in normalized:
        if len(tag) > 100:
            return False, "Each tag must be at most 100 characters."
    return True, None


def has_useful_identifier(data: dict) -> bool:
    fields = ("email", "phone", "company", "first_name", "last_name", "source_ref")
    for field in fields:
        value = data.get(field)
        if value is not None and str(value).strip():
            return True
    return False


def validate_score(score) -> tuple[bool, str | None]:
    if score is None or score == "":
        return True, None
    try:
        value = int(score)
    except (TypeError, ValueError):
        return False, "Score must be an integer."
    if value < 0 or value > 100:
        return False, "Score must be between 0 and 100."
    return True, None


def validate_lead_source(source: str | None) -> bool:
    return source in LEAD_SOURCES


def validate_lead_status(status: str | None) -> bool:
    return status in LEAD_STATUSES


def normalize_lead_data(data: dict) -> dict:
    result = dict(data)
    if result.get("email"):
        result["email"] = normalize_email(result["email"])
    if result.get("source"):
        result["source"] = str(result["source"]).strip().lower()
    if "tags" in result:
        result["tags"] = normalize_tags(result.get("tags"))
    for field in ("first_name", "last_name", "phone", "company", "title", "website", "linkedin_url", "source_ref", "notes", "score_reason"):
        if field in result and isinstance(result[field], str):
            result[field] = result[field].strip() or None
    return result


def validate_lead_fields(data: dict, *, require_identifier: bool = False) -> tuple[bool, str | None]:
    data = normalize_lead_data(data)

    if require_identifier and not has_useful_identifier(data):
        return False, "At least one identifier is required (email, phone, company, name, or source ref)."

    email = data.get("email")
    if email and not validate_email(email):
        return False, "Invalid email address."

    ok, msg = validate_score(data.get("score"))
    if not ok:
        return False, msg

    if data.get("website") and not validate_url_field(data["website"]):
        return False, "Invalid website URL."
    if data.get("linkedin_url") and not validate_url_field(data["linkedin_url"]):
        return False, "Invalid LinkedIn URL."

    ok, msg = validate_tags(data.get("tags", []))
    if not ok:
        return False, msg

    if data.get("source") and not validate_lead_source(data["source"]):
        return False, "Invalid lead source."

    return True, None


def sanitize_csv_value(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in ("=", "+", "-", "@"):
        return "'" + text
    return text
