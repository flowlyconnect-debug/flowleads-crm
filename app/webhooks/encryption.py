from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


class WebhookEncryptionError(RuntimeError):
    pass


def _get_fernet_key() -> bytes:
    key = current_app.config.get("WEBHOOK_ENCRYPTION_KEY")
    if not key:
        # In tests, TestingConfig sets a temporary key; in development/production this must be provided
        test_key = current_app.config.get("_WEBHOOK_TEST_FERNET_KEY")
        if test_key:
            key = test_key
        if not key:
            raise WebhookEncryptionError(
                "WEBHOOK_ENCRYPTION_KEY is required for webhook endpoint storage."
            )
    if isinstance(key, str):
        key = key.strip().encode("ascii")
    return key


def _fernet() -> Fernet:
    return Fernet(_get_fernet_key())


def encrypt_webhook_secret(value: str | None) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_webhook_secret(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise WebhookEncryptionError("Failed to decrypt webhook secret.") from exc

