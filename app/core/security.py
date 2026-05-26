import hashlib
import re
import secrets
import string

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from flask import current_app

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    value = email.strip().lower()
    return value or None


def validate_email(email: str | None) -> bool:
    normalized = normalize_email(email)
    if not normalized or len(normalized) > 255:
        return False
    return bool(EMAIL_RE.match(normalized))


def validate_password(password: str | None, min_length: int | None = None) -> tuple[bool, str | None]:
    if password is None or not password.strip():
        return False, "Password is required."
    min_len = min_length or current_app.config.get("PASSWORD_MIN_LENGTH", 12)
    if len(password) < min_len:
        return False, f"Password must be at least {min_len} characters."
    return True, None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_backup_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "-".join(parts)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def generate_api_key(*, test_mode: bool = False) -> tuple[str, str, str]:
    """Return (full_key, key_hash, key_prefix). Full key is shown once only."""
    label = "fl_test" if test_mode else "fl_live"
    token = secrets.token_urlsafe(32)
    full_key = f"{label}_{token}"
    return full_key, hash_api_key(full_key), full_key[:8]


def hash_backup_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def verify_backup_code(code: str, code_hash: str) -> bool:
    if not code or not code_hash:
        return False
    return hash_backup_code(code) == code_hash


def get_password_reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt=current_app.config.get("PASSWORD_RESET_SALT", "password-reset-salt"),
    )


def generate_password_reset_token(email: str) -> str:
    serializer = get_password_reset_serializer()
    return serializer.dumps(normalize_email(email))


def verify_password_reset_token(token: str, max_age: int | None = None) -> str | None:
    if not token:
        return None
    serializer = get_password_reset_serializer()
    age = max_age or current_app.config.get("PASSWORD_RESET_MAX_AGE", 3600)
    try:
        return serializer.loads(token, max_age=age)
    except (BadSignature, SignatureExpired):
        return None


def validate_slug(slug: str | None) -> bool:
    if not slug or len(slug) > 120:
        return False
    return bool(SLUG_RE.match(slug))
