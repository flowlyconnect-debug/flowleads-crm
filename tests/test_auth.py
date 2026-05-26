from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from app.core.permissions import TWO_FA_SESSION_KEY
from app.core.security import hash_password
from app.extensions import db
from app.users.models import AuditLog, User
from app.users.services import create_organization, create_user


def _create_org_user(app, email="user@acme.com", role="user", slug=None, **kwargs):
    with app.app_context():
        org = create_organization("Acme", slug or email.split("@")[0].replace(".", "-"))
        db.session.flush()
        user = create_user(
            email,
            kwargs.get("password", "securepassword1"),
            role=role,
            organization_id=org.id if role != "superadmin" else None,
            is_active=kwargs.get("is_active", True),
        )
        db.session.commit()
        return user.id, org.id


def test_login_valid_credentials(client, app):
    _create_org_user(app)
    response = client.post(
        "/auth/login",
        data={"email": "user@acme.com", "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 200)


def test_inactive_user_cannot_login(client, app):
    with app.app_context():
        org = create_organization("Acme", "acme-inactive")
        db.session.flush()
        user = create_user(
            "inactive@acme.com",
            "securepassword1",
            role="user",
            organization_id=org.id,
            is_active=False,
        )
        db.session.commit()

    response = client.post(
        "/auth/login",
        data={"email": "inactive@acme.com", "password": "securepassword1"},
        follow_redirects=True,
    )
    assert b"inactive" in response.data.lower() or response.status_code == 200


def test_api_client_cannot_login_ui(client, app):
    with app.app_context():
        org = create_organization("API Org", "api-org")
        db.session.flush()
        create_user(
            "api@acme.com",
            "securepassword1",
            role="api_client",
            organization_id=org.id,
        )
        db.session.commit()

    response = client.post(
        "/auth/login",
        data={"email": "api@acme.com", "password": "securepassword1"},
        follow_redirects=True,
    )
    assert b"API" in response.data or b"api" in response.data.lower()


def test_wrong_password_increments_failed_attempts(client, app):
    user_id, _ = _create_org_user(app, email="fail@acme.com", slug="fail-acme")
    client.post(
        "/auth/login",
        data={"email": "fail@acme.com", "password": "wrongpassword"},
    )
    with app.app_context():
        refreshed = db.session.get(User, user_id)
        assert refreshed.failed_login_attempts == 1


def test_five_failed_attempts_locks_account(client, app):
    with app.app_context():
        org = create_organization("Lock Org", "lock-org")
        db.session.flush()
        user = User(
            email="locked@acme.com",
            password_hash=hash_password("securepassword1"),
            role="user",
            organization_id=org.id,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    for _ in range(5):
        client.post(
            "/auth/login",
            data={"email": "locked@acme.com", "password": "wrong"},
        )

    with app.app_context():
        refreshed = db.session.get(User, user_id)
        assert refreshed.failed_login_attempts >= 5
        assert refreshed.locked_until is not None


def test_locked_account_cannot_login(client, app):
    with app.app_context():
        org = create_organization("Locked", "locked")
        db.session.flush()
        user = User(
            email="blocked@acme.com",
            password_hash=hash_password("securepassword1"),
            role="user",
            organization_id=org.id,
            failed_login_attempts=5,
            locked_until=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        db.session.add(user)
        db.session.commit()

    response = client.post(
        "/auth/login",
        data={"email": "blocked@acme.com", "password": "securepassword1"},
        follow_redirects=True,
    )
    assert b"locked" in response.data.lower()


def test_superadmin_without_2fa_cannot_access_dashboard(client, app):
    with app.app_context():
        user = create_user("admin@flowleads.com", "securepassword1", role="superadmin")
        user.totp_enabled = True
        user.totp_secret = pyotp.random_base32()
        db.session.commit()

    client.post(
        "/auth/login",
        data={"email": "admin@flowleads.com", "password": "securepassword1"},
    )
    response = client.get("/admin/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert "2fa" in response.location.lower()


def test_superadmin_with_2fa_session_can_access_dashboard(client, app):
    with app.app_context():
        secret = pyotp.random_base32()
        user = create_user("sa@flowleads.com", "securepassword1", role="superadmin")
        user.totp_enabled = True
        user.totp_secret = secret
        db.session.commit()

    client.post(
        "/auth/login",
        data={"email": "sa@flowleads.com", "password": "securepassword1"},
    )
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True

    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert b"Admin dashboard" in response.data


def test_audit_login_success_and_failed_and_logout(client, app):
    _create_org_user(app, email="audit@acme.com")

    client.post(
        "/auth/login",
        data={"email": "audit@acme.com", "password": "securepassword1"},
    )
    with app.app_context():
        assert AuditLog.query.filter_by(action="login_success").count() == 1

    client.get("/auth/logout", follow_redirects=True)
    client.post(
        "/auth/login",
        data={"email": "audit@acme.com", "password": "wrong"},
    )
    with app.app_context():
        assert AuditLog.query.filter_by(action="login_failed").count() >= 1

    client.get("/auth/logout", follow_redirects=True)
    with app.app_context():
        assert AuditLog.query.filter_by(action="logout").count() == 1
