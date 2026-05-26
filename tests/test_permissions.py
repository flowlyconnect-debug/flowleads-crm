"""Route permission and tenancy checks."""

from unittest.mock import patch

import pytest

from app.api.services import create_api_key, rotate_api_key
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.users.services import create_organization, create_user


def _login(client, email, password="securepassword1"):
    return client.post("/auth/login", data={"email": email, "password": password})


def _enable_2fa_session(client):
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True


@pytest.fixture
def org_users(app):
    with app.app_context():
        org_a = create_organization("Org A", "org-a-perm")
        org_b = create_organization("Org B", "org-b-perm")
        db.session.flush()
        admin_a = create_user("admin-a@org.com", "securepassword1", role="admin", organization_id=org_a.id)
        user_a = create_user("user-a@org.com", "securepassword1", role="user", organization_id=org_a.id)
        admin_b = create_user("admin-b@org.com", "securepassword1", role="admin", organization_id=org_b.id)
        sa = create_user("sa-perm@test.com", "securepassword1", role="superadmin")
        sa.totp_enabled = True
        api_user = create_user("api@org.com", "securepassword1", role="api_client", organization_id=org_a.id)
        db.session.commit()
        return {
            "org_a": org_a.id,
            "org_b": org_b.id,
            "admin_a": admin_a.email,
            "user_a": user_a.email,
            "admin_b": admin_b.email,
            "sa": sa.email,
            "api": api_user.email,
        }


def test_api_client_cannot_access_ui_routes(client, org_users):
    _login(client, org_users["api"])
    for path in ("/leads/", "/analytics/dashboard"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (302, 403, 404)


def test_admin_backup_routes_require_superadmin(client, org_users):
    _login(client, org_users["admin_a"])
    _enable_2fa_session(client)
    assert client.get("/admin/backups").status_code == 403


def test_superadmin_backup_requires_2fa(client, org_users):
    _login(client, org_users["sa"])
    assert client.get("/admin/backups").status_code == 302
    _enable_2fa_session(client)
    assert client.get("/admin/backups").status_code == 200


def test_admin_reports_require_superadmin_2fa(client, org_users):
    _login(client, org_users["admin_a"])
    assert client.get("/admin/reports").status_code in (302, 403)
    client.get("/auth/logout", follow_redirects=True)
    _login(client, org_users["sa"])
    assert client.get("/admin/reports").status_code == 302
    _enable_2fa_session(client)
    assert client.get("/admin/reports").status_code == 200


def test_organization_data_scoped(client, app, org_users):
    with app.app_context():
        from app.leads.models import Lead
        from app.leads.services import get_default_stage

        stage = get_default_stage(org_users["org_a"])
        lead_a = Lead(
            organization_id=org_users["org_a"],
            stage_id=stage.id,
            first_name="A",
            last_name="Lead",
            email="scoped-a@example.com",
            source="manual",
        )
        db.session.add(lead_a)
        db.session.commit()
        lead_a_id = lead_a.id

    _login(client, org_users["admin_b"])
    response = client.get(f"/leads/{lead_a_id}")
    assert response.status_code in (403, 404)


@patch("app.email.services._mailgun_send")
def test_rotate_api_key_cli(mock_send, runner, app, org_users):
    mock_send.return_value = (True, "msg", None)
    with app.app_context():
        _key, full = create_api_key(org_users["org_a"], "integration", test_mode=True)
        db.session.commit()
        key_id = _key.id

    result = runner.invoke(args=["rotate-api-key", str(key_id)])
    assert result.exit_code == 0
    assert full not in result.output
    assert len(result.output.strip().splitlines()) >= 2


@patch("app.email.services._mailgun_send")
def test_send_test_email_cli_success(mock_send, runner, app):
    mock_send.return_value = (True, "msg-id", None)
    result = runner.invoke(args=["send-test-email", "cli-test@example.com"])
    assert result.exit_code == 0
    assert "success" in result.output.lower()


@patch("app.email.services._mailgun_send")
def test_send_test_email_cli_failure(mock_send, runner, app):
    mock_send.return_value = (False, None, "provider error")
    result = runner.invoke(args=["send-test-email", "fail@example.com"])
    assert result.exit_code != 0
