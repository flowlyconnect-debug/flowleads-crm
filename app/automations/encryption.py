"""Encrypt sensitive automation config values (e.g. webhook header secrets)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


def _fernet() -> Fernet:
    secret = (current_app.config.get("SECRET_KEY") or "test-secret-key").encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt_value(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_value(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def encrypt_webhook_headers(headers: dict | None) -> dict:
    """Return encrypted_headers dict for storage in action_config."""
    if not headers:
        return {}
    encrypted = {}
    for key, value in headers.items():
        if value:
            encrypted[key] = encrypt_value(str(value))
    return encrypted


def decrypt_webhook_headers(encrypted_headers: dict | None) -> dict:
    if not encrypted_headers:
        return {}
    return {k: decrypt_value(v) for k, v in encrypted_headers.items()}
