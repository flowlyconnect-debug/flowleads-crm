"""TOTP helpers: normalization, validation, and safe verification logging."""

from __future__ import annotations

import base64
import binascii
import logging
import time
from datetime import datetime, timezone

import pyotp

from app.users.models import User

logger = logging.getLogger(__name__)

TOTP_VALID_WINDOW = 1


def normalize_totp_code(code: str | None) -> str | None:
    if code is None:
        return None
    stripped = code.strip().replace(" ", "")
    return stripped or None


def normalize_totp_secret(secret: str | None) -> str | None:
    if secret is None:
        return None
    normalized = secret.strip().upper().replace(" ", "")
    return normalized or None


def is_valid_base32_secret(secret: str) -> bool:
    normalized = normalize_totp_secret(secret)
    if not normalized:
        return False
    try:
        base64.b32decode(normalized, casefold=True)
        return True
    except (binascii.Error, ValueError):
        return False


def _log_verify_attempt(
    *,
    context: str,
    user_id: int | None,
    secret: str,
    code: str,
    success: bool,
) -> None:
    server_utc = datetime.now(timezone.utc).isoformat()
    secret_valid = is_valid_base32_secret(secret)
    log_fn = logger.info if success else logger.warning
    log_fn(
        "totp_verify context=%s user_id=%s success=%s secret_len=%d code_len=%d "
        "secret_base32_valid=%s valid_window=%d server_utc=%s",
        context,
        user_id,
        success,
        len(secret),
        len(code),
        secret_valid,
        TOTP_VALID_WINDOW,
        server_utc,
    )


def verify_totp_token(
    secret: str | None,
    token: str | None,
    *,
    context: str = "unknown",
    user_id: int | None = None,
) -> bool:
    """Verify a TOTP code. Never logs secrets or codes."""
    normalized_secret = normalize_totp_secret(secret)
    code = normalize_totp_code(token)
    if not normalized_secret or not code:
        logger.info(
            "totp_verify skipped context=%s user_id=%s reason=missing_input",
            context,
            user_id,
        )
        return False
    if not is_valid_base32_secret(normalized_secret):
        logger.warning(
            "totp_verify failed context=%s user_id=%s reason=invalid_base32_secret",
            context,
            user_id,
        )
        return False

    totp = pyotp.TOTP(normalized_secret)
    success = bool(totp.verify(code, valid_window=TOTP_VALID_WINDOW))
    _log_verify_attempt(
        context=context,
        user_id=user_id,
        secret=normalized_secret,
        code=code,
        success=success,
    )
    return success


def fresh_user_totp_secret(user: User) -> str | None:
    """Reload totp_secret from DB so verification uses persisted value."""
    from app.extensions import db

    db.session.refresh(user, attribute_names=["totp_secret", "totp_enabled"])
    return normalize_totp_secret(user.totp_secret)


def current_totp_interval() -> int:
    return int(time.time()) // 30
