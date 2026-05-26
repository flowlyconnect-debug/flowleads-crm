import base64
import io

import pyotp
import qrcode
from flask import current_app
from flask_login import current_user

from app.auth.models import BackupCode
from app.core.audit import log_audit
from app.core.permissions import clear_2fa_session, set_2fa_verified
from app.core.security import (
    generate_backup_code,
    hash_backup_code,
    verify_backup_code,
)
from app.extensions import db
from app.users.models import User
from app.users.services import UserServiceError, get_user_by_email, reset_login_attempts

BACKUP_CODE_COUNT = 10


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
    secret = generate_totp_secret()
    user.totp_secret = secret
    user.totp_enabled = False
    db.session.flush()
    uri = get_totp_uri(user, secret)
    qr_b64 = generate_qr_code_base64(uri)
    return secret, uri, qr_b64


def enable_totp(user: User, token: str) -> list[str]:
    if not user.totp_secret:
        raise UserServiceError("2FA is not initialized.", "totp_not_initialized")
    totp = pyotp.TOTP(user.totp_secret)
    if not token or not totp.verify(token.strip(), valid_window=1):
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


def verify_totp_login(user: User, token: str | None, backup: str | None = None) -> bool:
    if backup:
        return _verify_backup_code(user, backup)
    if not token or not user.totp_secret:
        return False
    totp = pyotp.TOTP(user.totp_secret)
    return totp.verify(token.strip(), valid_window=1)


def _verify_backup_code(user: User, code: str) -> bool:
    code_hash = hash_backup_code(code)
    record = BackupCode.query.filter_by(
        user_id=user.id, code_hash=code_hash, used=False
    ).first()
    if not record:
        return False
    record.used = True
    db.session.commit()
    return True


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
