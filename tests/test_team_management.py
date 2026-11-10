"""Org-admin self-service teammate management: invite, resend, (de)activate."""
from unittest.mock import patch

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.users.models import User
from app.users.services import UserServiceError, create_organization, create_user, set_user_active
from app.users.team_services import invite_teammate, resend_teammate_invite


def _setup_org(app, slug="team-org"):
    with app.app_context():
        org = create_organization("Team Org", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        other = create_organization("Other", f"{slug}-other")
        db.session.flush()
        other_admin = create_user(
            f"other-admin-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=other.id,
        )
        db.session.commit()
        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "other_org_id": other.id,
            "other_admin_id": other_admin.id,
        }


def _login(client, email):
    response = client.post(
        "/auth/login", data={"email": email, "password": "securepassword1"}, follow_redirects=False
    )
    assert response.status_code == 302


def _bypass_2fa(client, email):
    user = User.query.filter_by(email=email).one()
    user.totp_enabled = True
    db.session.commit()
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True


@patch("app.email.services._mailgun_send")
def test_invite_teammate_creates_user_and_sends_email(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    ctx = _setup_org(app, "invite-ok")
    with app.app_context():
        actor = db.session.get(User, ctx["admin_id"])
        user = invite_teammate(ctx["org_id"], "newbie@example.com", "user", actor=actor)
        db.session.commit()
        assert user.organization_id == ctx["org_id"]
        assert user.role == "user"
        mock_send.assert_called_once()


def test_invite_teammate_rejects_invalid_role(app):
    ctx = _setup_org(app, "invite-bad-role")
    with app.app_context():
        actor = db.session.get(User, ctx["admin_id"])
        try:
            invite_teammate(ctx["org_id"], "x@example.com", "superadmin", actor=actor)
            assert False, "expected UserServiceError"
        except UserServiceError as exc:
            assert exc.code == "invalid_role"


@patch("app.email.services._mailgun_send")
def test_resend_invite_cross_tenant_denied(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    ctx = _setup_org(app, "resend-cross")
    with app.app_context():
        admin = db.session.get(User, ctx["admin_id"])
        teammate = invite_teammate(ctx["org_id"], "member@example.com", "user", actor=admin)
        db.session.commit()
        other_admin = db.session.get(User, ctx["other_admin_id"])
        try:
            resend_teammate_invite(teammate, actor=other_admin)
            assert False, "expected cross-tenant error"
        except UserServiceError as exc:
            assert exc.code == "cross_tenant"


def test_set_user_active_blocks_self_deactivate(app):
    ctx = _setup_org(app, "self-deactivate")
    with app.app_context():
        admin = db.session.get(User, ctx["admin_id"])
        try:
            set_user_active(admin, False, actor=admin)
            assert False, "expected self_deactivate error"
        except UserServiceError as exc:
            assert exc.code == "self_deactivate"


@patch("app.email.services._mailgun_send")
def test_team_page_lists_members_and_invite_form_works(mock_send, client, app):
    mock_send.return_value = (True, "<msg@test>", None)
    ctx = _setup_org(app, "ui-flow")
    _login(client, ctx["admin_email"])
    _bypass_2fa(client, ctx["admin_email"])

    list_resp = client.get("/settings/team")
    assert list_resp.status_code == 200
    assert ctx["admin_email"] in list_resp.get_data(as_text=True)

    invite_resp = client.post(
        "/settings/team/invite",
        data={"email": "colleague@example.com", "role": "user"},
        follow_redirects=True,
    )
    assert invite_resp.status_code == 200
    with app.app_context():
        teammate = User.query.filter_by(email="colleague@example.com").one()
        assert teammate.organization_id == ctx["org_id"]
        assert teammate.is_active is True
        teammate_id = teammate.id

    deactivate_resp = client.post(
        f"/settings/team/{teammate_id}/deactivate", follow_redirects=True
    )
    assert deactivate_resp.status_code == 200
    with app.app_context():
        assert db.session.get(User, teammate_id).is_active is False

    reactivate_resp = client.post(
        f"/settings/team/{teammate_id}/reactivate", follow_redirects=True
    )
    assert reactivate_resp.status_code == 200
    with app.app_context():
        assert db.session.get(User, teammate_id).is_active is True


def test_team_page_requires_admin_role(client, app):
    ctx = _setup_org(app, "ui-forbidden")
    with app.app_context():
        create_user(
            "plain-user@test.com", "securepassword1", role="user", organization_id=ctx["org_id"]
        )
        db.session.commit()
    _login(client, "plain-user@test.com")
    resp = client.get("/settings/team")
    assert resp.status_code == 403


@patch("app.email.services._mailgun_send")
def test_cannot_deactivate_user_from_another_organization_via_route(mock_send, client, app):
    mock_send.return_value = (True, "<msg@test>", None)
    ctx = _setup_org(app, "ui-cross-tenant")
    with app.app_context():
        other_user = create_user(
            "other-member@test.com",
            "securepassword1",
            role="user",
            organization_id=ctx["other_org_id"],
        )
        db.session.commit()
        other_user_id = other_user.id

    _login(client, ctx["admin_email"])
    _bypass_2fa(client, ctx["admin_email"])

    resp = client.post(f"/settings/team/{other_user_id}/deactivate")
    assert resp.status_code == 404
