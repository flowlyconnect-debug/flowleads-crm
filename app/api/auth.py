from datetime import datetime, timezone
from functools import wraps

from flask import g, request

from app.api.models import APIKey
from app.core.errors import json_error
from app.core.security import hash_api_key
from app.extensions import db


def extract_api_key() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            return token
    header_key = request.headers.get("X-API-Key", "").strip()
    if header_key:
        return header_key
    return None


def authenticate_api_key(raw_key: str | None) -> tuple[APIKey | None, str | None]:
    if not raw_key:
        return None, "missing_api_key"

    key_hash = hash_api_key(raw_key)
    api_key = APIKey.query.filter_by(key_hash=key_hash).first()
    if not api_key:
        return None, "invalid_api_key"

    if api_key.revoked_at is not None or not api_key.is_active:
        return None, "inactive_api_key"

    if api_key.is_expired():
        return None, "expired_api_key"

    return api_key, None


def record_api_key_usage(api_key: APIKey) -> None:
    api_key.last_used_at = datetime.now(timezone.utc)
    api_key.request_count = (api_key.request_count or 0) + 1
    db.session.flush()


def set_api_auth_context(api_key: APIKey) -> None:
    g.api_key = api_key
    g.api_key_id = api_key.id
    g.organization_id = api_key.organization_id
    g.organization = api_key.organization
    g.key_name = api_key.name
    g.api_key_rate_key = f"api_key:{api_key.id}"


def require_api_key(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        raw_key = extract_api_key()
        api_key, error_code = authenticate_api_key(raw_key)
        if error_code:
            messages = {
                "missing_api_key": "API key is required.",
                "invalid_api_key": "Invalid API key.",
                "expired_api_key": "API key has expired.",
                "inactive_api_key": "API key is inactive or revoked.",
            }
            return json_error(error_code, messages.get(error_code, "Unauthorized."), 401)

        set_api_auth_context(api_key)
        record_api_key_usage(api_key)
        db.session.commit()
        return view(*args, **kwargs)

    return wrapped
