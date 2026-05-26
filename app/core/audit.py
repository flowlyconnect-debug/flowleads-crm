from flask import request

from app.extensions import db
from app.users.models import AuditLog

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "reset_token",
        "totp_secret",
        "backup_code",
        "backup_codes",
        "mailgun_api_key",
        "api_key",
        "secret",
    }
)


def _sanitize_metadata(metadata: dict | None) -> dict | None:
    if not metadata:
        return None
    cleaned = {}
    for key, value in metadata.items():
        lower_key = str(key).lower()
        if lower_key in SENSITIVE_KEYS or any(s in lower_key for s in ("password", "secret", "token", "api_key")):
            continue
        cleaned[key] = value
    return cleaned or None


def log_audit(
    action: str,
    *,
    user_id: int | None = None,
    organization_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    metadata: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user_id,
        organization_id=organization_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=request.remote_addr if request else None,
        user_agent=(request.headers.get("User-Agent", "")[:512] if request else None),
        metadata_json=_sanitize_metadata(metadata),
    )
    db.session.add(entry)
    return entry
