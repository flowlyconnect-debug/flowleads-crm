from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pyotp
import pytest
from app.auth.models import PasswordResetToken
from app.auth.services import (
    consume_password_reset_token,
    get_valid_password_reset_token,
    issue_password_reset_token,
)
from app.core.permissions import TWO_FA_SESSION_KEY
from app.core.security import check_password, hash_password
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
    assert "Hallintapaneeli" in response.get_data(as_text=True)


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
    assert "Kohteesta kauppaan yhdessä paikassa." in html
    assert "Esimerkkinäkymä" in html
    # Interactive demo: targets, leads + sequence, call list and proposal
    assert "data-lp-flow" in html
    assert "Priorisointi oppii valinnoistasi." in html
    assert "Soittolista" in html and "Tarjous ja kauppa" in html
    assert "js/login-demo.js" in html
    # Target customers, pricing and trial
    assert "Tehty pienille remonttiurakoitsijoille" in html
    assert 'class="lp-trades"' in html and "Lukot ja kulunvalvonta" in html
    # the hero shows the product (renovation timeline), not a checklist
    assert 'class="lp-ledger"' in html and "pts__m" in html
    assert "kuukausihinta" not in html
    assert "Maksuton kokeilu" not in html and "maksuton kokeilu" not in html
    assert "Kaikki taloyhtiöiden urakkatyypit" in html
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


# --- Password reset: single-use, expiring token, simulated Mailgun delivery ---


@patch("app.email.services._mailgun_send")
def test_reset_password_request_sends_simulated_email(mock_send, client, app):
    mock_send.return_value = (True, "<reset@test>", None)
    user_id, _org_id = _create_org_user(app, email="reset1@acme.com")

    response = client.post(
        "/auth/reset-password",
        data={"email": "reset1@acme.com"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to_email"] == "reset1@acme.com"
    assert "/auth/reset-password/" in kwargs["body_html"]

    with app.app_context():
        assert PasswordResetToken.query.filter_by(user_id=user_id).count() == 1


@patch("app.email.services._mailgun_send")
def test_reset_password_request_handles_simulated_mailgun_failure(mock_send, client, app):
    """A simulated Mailgun outage must not crash the request, nor leak whether the
    account exists — the user still sees the generic confirmation flash."""
    mock_send.return_value = (False, None, "Simulated Mailgun outage")
    _create_org_user(app, email="reset-fail@acme.com")

    response = client.post(
        "/auth/reset-password",
        data={"email": "reset-fail@acme.com"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "lähetimme siihen ohjeet".encode() in response.data


def test_reset_token_is_single_use(app):
    with app.app_context():
        org = create_organization("SingleUse", "single-use-org")
        db.session.flush()
        user = create_user("single-use@acme.com", "securepassword1", role="user", organization_id=org.id)
        db.session.commit()

        token = issue_password_reset_token(user)
        db.session.commit()

        record = get_valid_password_reset_token(token)
        assert record is not None

        consume_password_reset_token(record)
        db.session.commit()

        # The same token must never validate again.
        assert get_valid_password_reset_token(token) is None


def test_reset_token_expires(app):
    with app.app_context():
        org = create_organization("Expiring", "expiring-org")
        db.session.flush()
        user = create_user("expiring@acme.com", "securepassword1", role="user", organization_id=org.id)
        db.session.commit()

        token = issue_password_reset_token(user)
        db.session.commit()

        record = PasswordResetToken.query.filter_by(user_id=user.id).one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()

        assert get_valid_password_reset_token(token) is None


def test_requesting_new_reset_invalidates_previous_token(app):
    with app.app_context():
        org = create_organization("Renew", "renew-org")
        db.session.flush()
        user = create_user("renew@acme.com", "securepassword1", role="user", organization_id=org.id)
        db.session.commit()

        first_token = issue_password_reset_token(user)
        db.session.commit()
        assert get_valid_password_reset_token(first_token) is not None

        second_token = issue_password_reset_token(user)
        db.session.commit()

        assert get_valid_password_reset_token(first_token) is None
        assert get_valid_password_reset_token(second_token) is not None


@patch("app.auth.routes.EmailService.send_password_reset_email")
def test_reset_password_full_flow_via_routes(mock_send, client, app):
    mock_send.return_value = {"success": True, "message_id": "<reset@test>", "error": None}
    _create_org_user(app, email="fullflow@acme.com", password="oldpassword1")

    client.post("/auth/reset-password", data={"email": "fullflow@acme.com"})
    reset_url = mock_send.call_args.kwargs["reset_url"]
    token = reset_url.rsplit("/", 1)[-1]

    response = client.post(
        f"/auth/reset-password/{token}",
        data={"password": "brandnewpassword1", "password_confirm": "brandnewpassword1"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        user = User.query.filter_by(email="fullflow@acme.com").one()
        assert check_password("brandnewpassword1", user.password_hash)
        assert not check_password("oldpassword1", user.password_hash)

    # The link cannot be used a second time, even though it has not expired.
    second_attempt = client.get(f"/auth/reset-password/{token}", follow_redirects=True)
    assert "Linkki ei kelpaa".encode() in second_attempt.data
