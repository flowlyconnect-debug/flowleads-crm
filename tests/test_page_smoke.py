"""Smoke tests for primary CRM UI pages."""

from __future__ import annotations

import pytest

from app.extensions import db
from app.leads.services import LeadService, get_default_stage
from app.users.models import User
from app.users.services import create_organization, create_user

MAIN_PAGES = (
    "/dashboard",
    "/leads",
    "/tasks",
    "/calendar",
    "/sequences",
    "/proposals",
    "/forms",
    "/leads/pipeline",
)


def _login(client, email: str, password: str = "securepassword1") -> None:
    response = client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 302


@pytest.fixture
def org_admin(app):
    with app.app_context():
        org = create_organization("Smoke Org", "smoke-org")
        db.session.flush()
        admin = create_user(
            "smoke-admin@test.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        db.session.commit()
        return {"org_id": org.id, "email": admin.email}


@pytest.fixture
def superadmin(app):
    with app.app_context():
        org = create_organization("Smoke Super Org", "smoke-super-org")
        db.session.flush()
        user = create_user(
            "smoke-super@test.com",
            "securepassword1",
            role="superadmin",
            organization_id=None,
        )
        user.totp_enabled = True
        db.session.commit()
        return {"org_id": org.id, "email": user.email}


def _assert_page_ok(response):
    body = response.get_data(as_text=True)
    assert response.status_code == 200, body[:500]
    assert "Something went wrong" not in body
    assert "An unexpected error occurred" not in body


@pytest.mark.parametrize("path", MAIN_PAGES)
def test_admin_main_pages_render(client, app, org_admin, path):
    _login(client, org_admin["email"])
    response = client.get(path, follow_redirects=True)
    _assert_page_ok(response)


@pytest.mark.parametrize("path", MAIN_PAGES)
def test_superadmin_main_pages_render(client, app, superadmin, path):
    _login(client, superadmin["email"])
    with client.session_transaction() as sess:
        sess["two_fa_verified"] = True
    org_id = superadmin["org_id"]
    response = client.get(f"{path}?organization_id={org_id}", follow_redirects=True)
    _assert_page_ok(response)


def test_superadmin_invalid_org_redirects_to_dashboard(client, app, superadmin):
    _login(client, superadmin["email"])
    response = client.get("/leads?organization_id=999999", follow_redirects=False)
    assert response.status_code == 302
    assert "/dashboard" in response.headers.get("Location", "")


def test_leads_list_with_data_and_filters(client, app, org_admin):
    with app.app_context():
        admin = User.query.filter_by(email=org_admin["email"]).first()
        stage = get_default_stage(org_admin["org_id"])
        LeadService.create(
            {"email": "lead@smoke.test", "first_name": "Smoke", "company": "Oy"},
            org_admin["org_id"],
            admin.id,
            actor_role="admin",
        )
        db.session.commit()
        stage_id = stage.id

    _login(client, org_admin["email"])
    response = client.get(f"/leads?created_from=2026-01-01&stage_id={stage_id}")
    _assert_page_ok(response)
