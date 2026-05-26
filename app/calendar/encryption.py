"""Encrypt OAuth tokens for calendar connections using Fernet."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


class CalendarEncryptionError(RuntimeError):
    pass


def _get_fernet_key() -> bytes:
    key = current_app.config.get("CALENDAR_ENCRYPTION_KEY")
    if not key:
        if current_app.config.get("TESTING"):
            key = current_app.config.get("_CALENDAR_TEST_FERNET_KEY")
        if not key:
            raise CalendarEncryptionError(
                "CALENDAR_ENCRYPTION_KEY is required for calendar OAuth token storage."
            )
    if isinstance(key, str):
        key = key.strip().encode("ascii")
    return key


def _fernet() -> Fernet:
    return Fernet(_get_fernet_key())


def encrypt_token(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_token(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise CalendarEncryptionError("Failed to decrypt calendar token.") from exc
