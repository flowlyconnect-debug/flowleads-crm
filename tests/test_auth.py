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


def test_login_page_shows_form_and_public_product_intro(client):
    response = client.get("/auth/login")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Sign-in form, field names and password reset stay available
    assert 'name="email"' in html
    assert 'name="password"' in html
    assert 'autocomplete="username"' in html
    assert 'autocomplete="current-password"' in html
    assert 'value="Kirjaudu sisään"' in html
    assert 'href="/auth/reset-password"' in html
    assert 'id="kirjaudu"' in html

    # Public product intro, example view and Flowly links
    assert "Flowly tuo uusia liidejä ja pitää nykyiset myyntikohteesi ajan tasalla." in html
    assert "Esimerkkinäkymä" in html
    assert "Pyydä esittely" in html
    assert 'href="https://flowlysolutions.net/yhteystiedot"' in html
    assert 'href="https://flowlysolutions.net"' in html
    assert "Flowly Solutions" in html


def test_login_page_shows_finnish_error_inside_form_card(client, app):
    _create_org_user(app, email="wrongpw@acme.com", slug="wrongpw")
    response = client.post(
        "/auth/login",
        data={"email": "wrongpw@acme.com", "password": "not-the-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Sähköposti tai salasana on virheellinen." in html
    assert "Invalid email or password." not in html
    assert 'role="alert"' in html
    assert 'aria-invalid="true"' in html
    # Entered email is kept
    assert 'value="wrongpw@acme.com"' in html
    # Error is rendered inside the login card, not as a page-level flash
    assert html.index('id="kirjaudu"') < html.index("Sähköposti tai salasana on virheellinen.")
    assert 'class="flash-messages"' not in html
