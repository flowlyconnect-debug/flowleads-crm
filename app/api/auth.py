from datetime import datetime, timezone
from functools import wraps

from flask import g, request

from app.api.models import APIKey
from app.core.errors import json_error
from app.core.security import hash_api_key
from app.extensions import db
from app.users.models import Organization


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


_AUTH_ERROR_MESSAGES = {
    "missing_api_key": "API key is required.",
    "invalid_api_key": "Invalid API key.",
    "expired_api_key": "API key has expired.",
    "inactive_api_key": "API key is inactive or revoked.",
}


def require_api_key(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        raw_key = extract_api_key()
        api_key, error_code = authenticate_api_key(raw_key)
        if error_code:
            return json_error(error_code, _AUTH_ERROR_MESSAGES.get(error_code, "Unauthorized."), 401)

        set_api_auth_context(api_key)
        record_api_key_usage(api_key)
        db.session.commit()
        return view(*args, **kwargs)

    return wrapped


def authenticate_job_token(raw_key: str | None) -> tuple[int | None, str | None]:
    """Resolve a short-lived, single-job-scoped n8n token (see
    claim_pending_jobs_for_n8n) to an organization_id, instead of the org's
    permanent API key. Only valid while the job it was minted for is still
    "running" and within its TTL."""
    if not raw_key:
        return None, "missing_api_key"

    from app.search.models import SearchJob

    token_hash = hash_api_key(raw_key)
    job = SearchJob.query.filter_by(n8n_token_hash=token_hash).first()
    if job is None or job.status != "running":
        return None, "invalid_api_key"

    expires_at = job.n8n_token_expires_at
    if expires_at is None:
        return None, "invalid_api_key"
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires_at:
        return None, "expired_api_key"

    return job.organization_id, None


def set_job_token_auth_context(organization_id: int) -> None:
    g.api_key = None
    g.api_key_id = None
    g.organization_id = organization_id
    g.organization = db.session.get(Organization, organization_id)
    g.key_name = "n8n-job-token"
    g.api_key_rate_key = f"job_token_org:{organization_id}"


def require_api_key_or_job_token(view):
    """Like require_api_key, but also accepts a job-scoped n8n token — used
    only on the lead-creation endpoints n8n actually posts to. Every other
    /api/v1/* route stays on the permanent API key alone (require_api_key)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        raw_key = extract_api_key()
        api_key, error_code = authenticate_api_key(raw_key)
        if api_key is not None:
            set_api_auth_context(api_key)
            record_api_key_usage(api_key)
            db.session.commit()
            return view(*args, **kwargs)

        organization_id, job_error_code = authenticate_job_token(raw_key)
        if organization_id is not None:
            set_job_token_auth_context(organization_id)
            db.session.commit()
            return view(*args, **kwargs)

        final_error = job_error_code or error_code or "invalid_api_key"
        return json_error(final_error, _AUTH_ERROR_MESSAGES.get(final_error, "Unauthorized."), 401)

    return wrapped
