import time

import pyotp

from app.auth.services import (
    _regenerate_backup_codes,
    enable_totp,
    generate_totp_secret,
    setup_totp,
    verify_totp_login,
)
from app.auth.totp_utils import (
    is_valid_base32_secret,
    normalize_totp_code,
    normalize_totp_secret,
    verify_totp_token,
)
from app.extensions import db
from app.users.models import User
from app.users.services import create_user


def test_generate_secret_is_valid_base32():
    secret = generate_totp_secret()
    assert is_valid_base32_secret(secret)


def test_verify_totp_token_accepts_current_code():
    secret = generate_totp_secret()
    totp = pyotp.TOTP(secret)
    code = totp.now()
    assert verify_totp_token(secret, code, context="test") is True


def test_verify_totp_token_rejects_invalid_code():
    secret = generate_totp_secret()
    assert verify_totp_token(secret, "000000", context="test") is False


def test_verify_totp_token_strips_whitespace():
    secret = generate_totp_secret()
    totp = pyotp.TOTP(secret)
    code = totp.now()
    assert verify_totp_token(secret, f"  {code}  ", context="test") is True


def test_verify_totp_token_rejects_stale_code():
    secret = generate_totp_secret()
    totp = pyotp.TOTP(secret)
    stale_time = time.time() - (30 * 5)
    stale_code = totp.at(stale_time)
    assert verify_totp_token(secret, stale_code, context="test") is False


def test_normalize_totp_secret_uppercases():
    assert normalize_totp_secret(" abcd2345efgh6789 ") == "ABCD2345EFGH6789"


def test_setup_totp_persists_secret(client, app):
    with app.app_context():
        user = create_user("totp-setup@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        user_id = user.id

    client.post(
        "/auth/login",
        data={"email": "totp-setup@test.com", "password": "securepassword1"},
    )
    client.get("/auth/2fa/setup")

    with app.app_context():
        refreshed = db.session.get(User, user_id)
        assert refreshed.totp_secret is not None
        assert is_valid_base32_secret(refreshed.totp_secret)


def test_enable_totp_and_login_verify(client, app):
    with app.app_context():
        user = create_user("totp-flow@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        user_id = user.id
        secret = setup_totp(user)[0]
        code = pyotp.TOTP(secret).now()
        user = db.session.get(User, user_id)
        enable_totp(user, code)

    client.post(
        "/auth/login",
        data={"email": "totp-flow@test.com", "password": "securepassword1"},
    )
    fresh_code = pyotp.TOTP(secret).now()
    response = client.post(
        "/auth/2fa/verify",
        data={"token": fresh_code},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.location is not None


def test_backup_code_rejected_for_login(app):
    with app.app_context():
        user = create_user("totp-backup@test.com", "securepassword1", role="superadmin")
        secret = generate_totp_secret()
        user.totp_secret = secret
        user.totp_enabled = False
        db.session.flush()
        raw_codes = _regenerate_backup_codes(user)
        user.totp_enabled = True
        db.session.commit()

        assert verify_totp_login(user, raw_codes[0]) is False
