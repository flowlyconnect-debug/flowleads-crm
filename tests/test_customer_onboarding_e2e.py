"""End-to-end verification of the customer onboarding path."""

import pytest

from app.admin.onboarding_services import CustomerOnboardingError, create_customer
from app.api.models import APIKey
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead, PipelineStage
from app.search.models import SearchJob, SearchProfile
from app.streams.models import OrgLeadSettings
from app.users.models import Organization, User
from app.users.services import create_organization, create_user


def _onboard_customer(app, *, slug_suffix="e2e"):
    with app.app_context():
        actor = create_user(f"sa-{slug_suffix}@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        result = create_customer(
            actor=actor,
            organization_name=f"E2E Asiakas {slug_suffix}",
            admin_email=f"admin-{slug_suffix}@e2e.fi",
            admin_password="securepassword1",
            admin_name="E2E Admin",
            search_profile_name=f"Haku {slug_suffix}",
            regions=["Uusimaa", "Pirkanmaa"],
            remonttityyppi="Lukkoremontti / kulunvalvonta",
            source="oikotie",
            is_active=True,
        )
        db.session.commit()
        return {
            "org_id": result.organization.id,
            "org_slug": result.organization.slug,
            "admin_email": result.admin_user.email,
            "api_key": result.api_key_full,
            "profile_id": SearchProfile.query.filter_by(organization_id=result.organization.id).one().id,
            "job_id": SearchJob.query.filter_by(organization_id=result.organization.id).one().id,
        }


def _login(client, email, password="securepassword1"):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _login_superadmin(client, app, email="sa-ui@test.com"):
    with app.app_context():
        user = create_user(email, "securepassword1", role="superadmin")
        user.totp_enabled = True
        db.session.commit()
    client.post("/auth/login", data={"email": email, "password": "securepassword1"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True


def test_e2e_onboarding_creates_all_entities(app):
    ctx = _onboard_customer(app, slug_suffix="entities")

    with app.app_context():
        org = db.session.get(Organization, ctx["org_id"])
        assert org is not None
        assert org.is_active is True

        admin = User.query.filter_by(email=ctx["admin_email"]).one()
        assert admin.role == "admin"
        assert admin.organization_id == org.id

        api_key = APIKey.query.filter_by(organization_id=org.id, name="n8n").one()
        assert api_key.is_active is True
        assert api_key.revoked_at is None

        profile = SearchProfile.query.filter_by(organization_id=org.id).one()
        assert profile.crm_api_key == ctx["api_key"]
        assert profile.is_active is True

        job = SearchJob.query.filter_by(id=ctx["job_id"]).one()
        assert job.status == "pending"
        assert job.search_profile_id == profile.id

        stages = PipelineStage.query.filter_by(organization_id=org.id).all()
        assert len(stages) == 6

        settings = OrgLeadSettings.query.filter_by(organization_id=org.id).one()
        assert settings is not None


def test_e2e_n8n_job_payload_complete(app, client):
    ctx = _onboard_customer(app, slug_suffix="n8n-payload")

    response = client.get(
        "/api/v1/n8n/jobs?status=pending&limit=50",
        headers={"X-N8N-Secret": "test-n8n-secret"},
    )
    assert response.status_code == 200
    jobs = response.get_json()["jobs"]
    job = next(j for j in jobs if j["job_id"] == ctx["job_id"])

    assert job["organization_id"] == ctx["org_id"]
    assert job["profile_id"] == ctx["profile_id"]
    assert job["remonttityyppi"] == "Lukkoremontti / kulunvalvonta"
    assert job["regions"] == ["Uusimaa", "Pirkanmaa"]
    assert job["source"] == "oikotie"
    assert job["crm_api_key"] == ctx["api_key"]
    assert job["crm_endpoint"].endswith("/api/v1/leads")


def test_e2e_n8n_lead_submission(app, client):
    ctx = _onboard_customer(app, slug_suffix="lead-submit")

    payload = {
        "email": "liidi@yritys.fi",
        "first_name": "Matti",
        "last_name": "Meikäläinen",
        "company": "Testi Oy",
        "phone": "+358401234567",
        "source": "n8n",
        "source_ref": "oikotie-12345",
        "notes": "E2E testiliidi",
    }
    create_resp = client.post(
        "/api/v1/leads",
        json=payload,
        headers={"Authorization": f"Bearer {ctx['api_key']}"},
    )
    assert create_resp.status_code == 201
    body = create_resp.get_json()
    assert body["success"] is True
    assert body["data"]["action"] == "created"
    lead_id = body["data"]["lead"]["id"]

    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert lead.organization_id == ctx["org_id"]
        assert lead.email == "liidi@yritys.fi"
        assert lead.company == "Testi Oy"
        assert lead.source_ref == "oikotie-12345"
        default_stage = (
            PipelineStage.query.filter_by(organization_id=ctx["org_id"])
            .order_by(PipelineStage.order_index.asc())
            .first()
        )
        assert lead.stage_id == default_stage.id
        assert body["data"]["lead"]["stage"] == default_stage.name

    dup_resp = client.post(
        "/api/v1/leads",
        json={**payload, "notes": "päivitetty"},
        headers={"Authorization": f"Bearer {ctx['api_key']}"},
    )
    assert dup_resp.status_code == 200
    assert dup_resp.get_json()["data"]["action"] == "updated"

    with app.app_context():
        assert Lead.query.filter_by(organization_id=ctx["org_id"], source_ref="oikotie-12345").count() == 1


def test_e2e_admin_login_sees_own_data_only(app, client):
    ctx = _onboard_customer(app, slug_suffix="tenant-a")
    other = _onboard_customer(app, slug_suffix="tenant-b")

    client.post(
        "/api/v1/leads",
        json={
            "email": "oma@liidi.fi",
            "source": "n8n",
            "source_ref": "tenant-a-1",
        },
        headers={"Authorization": f"Bearer {ctx['api_key']}"},
    )
    client.post(
        "/api/v1/leads",
        json={
            "email": "muu@liidi.fi",
            "source": "n8n",
            "source_ref": "tenant-b-1",
        },
        headers={"Authorization": f"Bearer {other['api_key']}"},
    )

    _login(client, ctx["admin_email"])
    leads_resp = client.get("/leads/")
    assert leads_resp.status_code == 200
    page = leads_resp.get_data(as_text=True)
    assert "oma@liidi.fi" in page
    assert "muu@liidi.fi" not in page

    pipeline_resp = client.get("/leads/pipeline")
    assert pipeline_resp.status_code == 200
    pipeline_page = pipeline_resp.get_data(as_text=True)
    assert "oma@liidi.fi" in pipeline_page
    assert "muu@liidi.fi" not in pipeline_page

    settings_resp = client.get("/settings/search-profiles")
    assert settings_resp.status_code == 200
    settings_page = settings_resp.get_data(as_text=True)
    assert f"Haku tenant-a" in settings_page
    assert "Haku tenant-b" not in settings_page
    assert ctx["api_key"] not in settings_page

    api_keys_resp = client.get("/settings/api-keys")
    assert api_keys_resp.status_code == 200
    api_page = api_keys_resp.get_data(as_text=True)
    assert ctx["api_key"] not in api_page
    assert "n8n" in api_page


def test_e2e_admin_cannot_access_superadmin_routes(app, client):
    ctx = _onboard_customer(app, slug_suffix="no-sa")
    other = _onboard_customer(app, slug_suffix="no-sa-other")

    _login(client, ctx["admin_email"])

    for path in (
        "/admin/customers/new",
        "/admin/api-keys",
    ):
        response = client.get(path)
        assert response.status_code == 403, path

    dashboard_resp = client.get("/admin/dashboard")
    assert dashboard_resp.status_code == 200
    dashboard_page = dashboard_resp.get_data(as_text=True)
    assert "Luo uusi asiakas" not in dashboard_page
    assert "Luo organisaatio" not in dashboard_page

    cross_org_resp = client.get(f"/leads/?organization_id={other['org_id']}")
    assert cross_org_resp.status_code == 200
    assert "muu@liidi.fi" not in cross_org_resp.get_data(as_text=True)

    wrong_key_resp = client.post(
        "/api/v1/leads",
        json={"email": "cross@tenant.fi", "source": "n8n", "source_ref": "x-1"},
        headers={"Authorization": f"Bearer {other['api_key']}"},
    )
    assert wrong_key_resp.status_code == 201
    with app.app_context():
        lead = Lead.query.filter_by(email="cross@tenant.fi").one()
        assert lead.organization_id == other["org_id"]


def test_e2e_search_profile_update_preserves_crm_api_key(app, client):
    ctx = _onboard_customer(app, slug_suffix="profile-key")

    _login(client, ctx["admin_email"])
    with app.app_context():
        profile = SearchProfile.query.filter_by(organization_id=ctx["org_id"]).one()
        original_key = profile.crm_api_key

    update_resp = client.post(
        f"/settings/search-profiles/{ctx['profile_id']}",
        data={
            "name": "Päivitetty profiili",
            "remonttityyppi": "Lukkoremontti / kulunvalvonta",
            "regions": ["Uusimaa"],
            "schedule_description": "daily",
            "is_active": "y",
        },
        follow_redirects=True,
    )
    assert update_resp.status_code == 200

    with app.app_context():
        profile = SearchProfile.query.filter_by(organization_id=ctx["org_id"]).one()
        assert profile.crm_api_key == original_key
        assert profile.name == "Päivitetty profiili"


def test_e2e_search_profile_update_preserves_key_when_empty(app, client):
    ctx = _onboard_customer(app, slug_suffix="clear-key")

    _login(client, ctx["admin_email"])
    with app.app_context():
        original_key = SearchProfile.query.filter_by(organization_id=ctx["org_id"]).one().crm_api_key

    response = client.post(
        f"/settings/search-profiles/{ctx['profile_id']}",
        data={
            "name": "Avain säilyy",
            "remonttityyppi": "Lukkoremontti / kulunvalvonta",
            "regions": ["Uusimaa"],
            "schedule_description": "daily",
            "crm_api_key": "",
            "is_active": "y",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        profile = SearchProfile.query.filter_by(organization_id=ctx["org_id"]).one()
        assert profile.crm_api_key == original_key


def test_e2e_duplicate_email_rejected(app):
    with app.app_context():
        actor = create_user("sa-dup@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        create_customer(
            actor=actor,
            organization_name="Ensimmäinen Oy",
            admin_email="sama@email.fi",
            admin_password="securepassword1",
            admin_name=None,
            search_profile_name="Profiili 1",
            regions=["Uusimaa"],
            remonttityyppi="Putkiremontti",
        )
        db.session.commit()

        with pytest.raises(CustomerOnboardingError) as exc:
            create_customer(
                actor=actor,
                organization_name="Toinen Oy",
                admin_email="sama@email.fi",
                admin_password="securepassword1",
                admin_name=None,
                search_profile_name="Profiili 2",
                regions=["Pirkanmaa"],
                remonttityyppi="Putkiremontti",
            )
        assert exc.value.code == "duplicate_email"


def test_e2e_duplicate_org_name_gets_unique_slug(app):
    with app.app_context():
        actor = create_user("sa-slug@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        first = create_customer(
            actor=actor,
            organization_name="Sama Nimi Oy",
            admin_email="eka@sama.fi",
            admin_password="securepassword1",
            admin_name=None,
            search_profile_name="Profiili A",
            regions=["Uusimaa"],
            remonttityyppi="Putkiremontti",
        )
        db.session.commit()
        second = create_customer(
            actor=actor,
            organization_name="Sama Nimi Oy",
            admin_email="toka@sama.fi",
            admin_password="securepassword1",
            admin_name=None,
            search_profile_name="Profiili B",
            regions=["Pirkanmaa"],
            remonttityyppi="Putkiremontti",
        )
        db.session.commit()

        assert first.organization.slug == "sama-nimi-oy"
        assert second.organization.slug == "sama-nimi-oy-2"
        assert first.organization.id != second.organization.id


def test_e2e_superadmin_ui_onboarding_flow(client, app):
    _login_superadmin(client, app)

    post_resp = client.post(
        "/admin/customers/new",
        data={
            "organization_name": "UI Flow Oy",
            "admin_name": "UI Admin",
            "admin_email": "uiadmin@flow.fi",
            "temporary_password": "securepassword1",
            "search_profile_name": "UI profiili",
            "remonttityyppi": "Putkiremontti",
            "regions": ["Keski-Suomi"],
            "source": "oikotie",
            "is_active": "y",
        },
        follow_redirects=False,
    )
    assert post_resp.status_code == 302

    result_resp = client.get("/admin/customers/result")
    assert result_resp.status_code == 200
    page = result_resp.get_data(as_text=True)
    assert "UI Flow Oy" in page
    assert "fl_" in page

    with app.app_context():
        org = Organization.query.filter_by(name="UI Flow Oy").one()
        assert SearchJob.query.filter_by(organization_id=org.id, status="pending").count() == 1
