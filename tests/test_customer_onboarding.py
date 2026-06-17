from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import PipelineStage
from app.search.models import SearchJob, SearchProfile
from app.users.models import AuditLog, User
from app.users.services import create_user

from app.admin.onboarding_services import create_customer


def _superadmin(app):
    with app.app_context():
        user = create_user("sa-onboard@test.com", "securepassword1", role="superadmin")
        user.totp_enabled = True
        db.session.commit()
        return user.id


def _login_superadmin(client, app):
    _superadmin(app)
    client.post(
        "/auth/login",
        data={"email": "sa-onboard@test.com", "password": "securepassword1"},
    )
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True


def test_create_customer_service_creates_full_stack(app):
    with app.app_context():
        actor = create_user("actor@test.com", "securepassword1", role="superadmin")
        db.session.commit()

        result = create_customer(
            actor=actor,
            organization_name="Uusi Asiakas Oy",
            admin_email="admin@uusiasiakas.fi",
            admin_password="securepassword1",
            admin_name="Admin User",
            search_profile_name="Lukkoremontti Uusimaa",
            regions=["Uusimaa"],
            remonttityyppi="Lukkoremontti / kulunvalvonta",
            source="oikotie",
            is_active=True,
        )
        db.session.commit()

        org = result.organization
        assert org.name == "Uusi Asiakas Oy"
        assert org.slug == "uusi-asiakas-oy"
        assert org.is_active is True

        admin = User.query.filter_by(email="admin@uusiasiakas.fi").first()
        assert admin is not None
        assert admin.role == "admin"
        assert admin.organization_id == org.id

        profile = SearchProfile.query.filter_by(organization_id=org.id).one()
        assert profile.name == "Lukkoremontti Uusimaa"
        assert profile.remonttityyppi == "Lukkoremontti / kulunvalvonta"
        assert profile.regions == ["Uusimaa"]
        assert profile.source == "oikotie"
        assert profile.schedule_description == "daily"
        assert profile.is_active is True
        assert profile.crm_api_key == result.api_key_full
        assert result.api_key_full.startswith("fl_")

        stages = PipelineStage.query.filter_by(organization_id=org.id).all()
        assert len(stages) >= 5

        job = SearchJob.query.filter_by(organization_id=org.id, search_profile_id=profile.id).first()
        assert job is not None
        assert job.status == "pending"

        audit = AuditLog.query.filter_by(action="customer_onboarded", organization_id=org.id).first()
        assert audit is not None


def test_create_customer_generates_password_when_missing(app):
    with app.app_context():
        actor = create_user("actor2@test.com", "securepassword1", role="superadmin")
        db.session.commit()

        result = create_customer(
            actor=actor,
            organization_name="Auto Salasana Oy",
            admin_email="auto@salasana.fi",
            admin_password=None,
            admin_name=None,
            search_profile_name="Putkiremontti",
            regions=["Pirkanmaa"],
            remonttityyppi="Putkiremontti",
            is_active=True,
        )
        db.session.commit()

        assert result.temporary_password is not None
        assert len(result.temporary_password) >= 12


def test_n8n_jobs_include_onboarded_customer(app, client):
    with app.app_context():
        actor = create_user("actor3@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        result = create_customer(
            actor=actor,
            organization_name="N8N Ready Oy",
            admin_email="n8n@ready.fi",
            admin_password="securepassword1",
            admin_name=None,
            search_profile_name="N8N profiili",
            regions=["Uusimaa"],
            remonttityyppi="Lukkoremontti / kulunvalvonta",
            is_active=True,
        )
        db.session.commit()
        api_key = result.api_key_full

    response = client.get(
        "/api/v1/n8n/jobs?status=pending&limit=10",
        headers={"X-N8N-Secret": "test-n8n-secret", "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    jobs = response.get_json()["jobs"]
    match = [job for job in jobs if job.get("crm_api_key") == api_key]
    assert len(match) == 1
    assert match[0]["remonttityyppi"] == "Lukkoremontti / kulunvalvonta"
    assert match[0]["regions"] == ["Uusimaa"]
    assert match[0]["source"] == "oikotie"


def test_superadmin_onboarding_form_and_result(client, app):
    _login_superadmin(client, app)

    form_resp = client.get("/admin/customers/new")
    assert form_resp.status_code == 200
    assert b"Luo uusi asiakas" in form_resp.data

    post_resp = client.post(
        "/admin/customers/new",
        data={
            "organization_name": "Web Onboard Oy",
            "admin_name": "Web Admin",
            "admin_email": "webadmin@onboard.fi",
            "temporary_password": "securepassword1",
            "search_profile_name": "Web profiili",
            "remonttityyppi": "Putkiremontti",
            "regions": ["Varsinais-Suomi"],
            "source": "oikotie",
            "is_active": "y",
        },
        follow_redirects=False,
    )
    assert post_resp.status_code == 302
    assert "/admin/customers/result" in post_resp.headers["Location"]

    result_resp = client.get("/admin/customers/result")
    assert result_resp.status_code == 200
    page = result_resp.get_data(as_text=True)
    assert "Web Onboard Oy" in page
    assert "webadmin@onboard.fi" in page
    assert "Web profiili" in page
    assert "Valmis n8n-ajoon" in page

    second_view = client.get("/admin/customers/result")
    assert second_view.status_code == 302
