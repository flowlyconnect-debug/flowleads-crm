import base64
import hashlib
import io
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
import qrcode
from flask import current_app

from app.auth.models import BackupCode, PasswordResetToken
from app.auth.totp_utils import (
    fresh_user_totp_secret,
    normalize_totp_secret,
    verify_totp_token,
)
from app.core.audit import log_audit
from app.core.permissions import clear_2fa_session, set_2fa_verified
from app.core.security import generate_backup_code, hash_backup_code
from app.extensions import db
from app.users.models import User
from app.users.services import UserServiceError, get_user_by_email, reset_login_attempts

BACKUP_CODE_COUNT = 10


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def issue_password_reset_token(user: User, max_age_seconds: int | None = None) -> str:
    """Issue a fresh single-use, expiring reset token.

    Any previous outstanding (unused) tokens for this user are deleted first,
    so only the most recently requested link is ever valid.
    """
    now = datetime.now(timezone.utc)
    PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).delete()

    if max_age_seconds is None:
        max_age_seconds = current_app.config.get("PASSWORD_RESET_MAX_AGE", 3600)
    token = secrets.token_urlsafe(32)
    db.session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_reset_token(token),
            expires_at=now + timedelta(seconds=max_age_seconds),
        )
    )
    db.session.flush()
    return token


def get_valid_password_reset_token(token: str | None) -> PasswordResetToken | None:
    """Return the token row if it exists, is unused, and has not expired."""
    if not token:
        return None
    record = PasswordResetToken.query.filter_by(token_hash=_hash_reset_token(token)).first()
    if record is None or record.used_at is not None:
        return None
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires_at:
        return None
    return record


def consume_password_reset_token(record: PasswordResetToken) -> None:
    """Mark a token used so it can never be replayed."""
    record.used_at = datetime.now(timezone.utc)


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_uri(user: User, secret: str) -> str:
    issuer = current_app.config.get("MAILGUN_FROM_NAME", "FlowLeads")
    return pyotp.totp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=issuer)


def generate_qr_code_base64(uri: str) -> str:
    img = qrcode.make(uri)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def setup_totp(user: User) -> tuple[str, str, str]:
    secret = normalize_totp_secret(generate_totp_secret())
    if not secret:
        raise UserServiceError("Unable to initialize 2FA.", "totp_setup_failed")
    user.totp_secret = secret
    user.totp_enabled = False
    db.session.commit()
    uri = get_totp_uri(user, secret)
    qr_b64 = generate_qr_code_base64(uri)
    return secret, uri, qr_b64


def enable_totp(user: User, token: str) -> list[str]:
    secret = fresh_user_totp_secret(user)
    if not secret:
        raise UserServiceError("2FA is not initialized.", "totp_not_initialized")
    if not verify_totp_token(secret, token, context="enable", user_id=user.id):
        raise UserServiceError("Invalid verification code.", "invalid_totp")

    user.totp_enabled = True
    raw_codes = _regenerate_backup_codes(user)
    log_audit("2fa_enabled", user_id=user.id, organization_id=user.organization_id)
    db.session.commit()
    return raw_codes


def _regenerate_backup_codes(user: User) -> list[str]:
    BackupCode.query.filter_by(user_id=user.id).delete()
    raw_codes = []
    for _ in range(BACKUP_CODE_COUNT):
        code = generate_backup_code()
        raw_codes.append(code)
        db.session.add(BackupCode(user_id=user.id, code_hash=hash_backup_code(code)))
    return raw_codes


def verify_totp_login(user: User, token: str | None) -> bool:
    """Second login factor: the authenticator app's code, or one of the
    single-use backup codes shown when 2FA was enabled. Backup codes are the
    only self-service way back in when the phone is lost, now that mandatory
    2FA is enforced on every page."""
    secret = fresh_user_totp_secret(user)
    if not secret:
        return False
    if verify_totp_token(secret, token, context="login", user_id=user.id):
        return True
    return consume_backup_code(user, token)


def consume_backup_code(user: User, code: str | None) -> bool:
    """Accept an unused backup code exactly once (commits the use)."""
    candidate = (code or "").strip().upper().replace(" ", "")
    # codes look like ABCD-EFGH-IJKL; accept them typed without dashes too
    compact = candidate.replace("-", "")
    if len(compact) != 12 or not compact.isalnum():
        return False
    formatted = "-".join(compact[i : i + 4] for i in range(0, 12, 4))
    record = BackupCode.query.filter_by(
        user_id=user.id, code_hash=hash_backup_code(formatted), used=False
    ).first()
    if record is None:
        return False
    record.used = True
    remaining = BackupCode.query.filter_by(user_id=user.id, used=False).count() - 1
    log_audit(
        "2fa_backup_code_used",
        user_id=user.id,
        organization_id=user.organization_id,
        metadata={"remaining": max(remaining, 0)},
    )
    db.session.commit()
    return True


def remaining_backup_codes(user: User) -> int:
    return BackupCode.query.filter_by(user_id=user.id, used=False).count()


def complete_2fa_session(user: User) -> None:
    set_2fa_verified(True)


def authenticate_user(email: str, password: str) -> tuple[User | None, str | None]:
    from app.users.services import is_account_locked, record_failed_login

    user = get_user_by_email(email)
    if user is None:
        return None, "Invalid email or password."

    if not user.can_login_ui():
        if user.role == "api_client":
            return None, "API accounts cannot sign in to the web interface."
        return None, "This account is inactive."

    if user.organization is not None and not user.organization.is_active:
        # the whole customer has been switched off by the platform
        return None, "Organisaatiosi tili ei ole käytössä. Ota yhteyttä FlowLeadsin ylläpitoon."

    if is_account_locked(user):
        return None, "Account is temporarily locked. Try again later."

    from app.core.security import check_password

    if not check_password(password, user.password_hash):
        record_failed_login(user)
        db.session.commit()
        return None, "Invalid email or password."

    reset_login_attempts(user)
    db.session.commit()
    return user, None


def logout_user_session() -> None:
    clear_2fa_session()
