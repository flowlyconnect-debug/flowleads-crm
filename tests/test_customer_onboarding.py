from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import PipelineStage
from app.search.models import SearchJob, SearchProfile
from app.users.models import AuditLog, Organization, User
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
        assert profile.needs_rematch is True
        assert profile.crm_api_key is None
        assert result.search_profile.id == profile.id
        # the superadmin knows the password, so the customer must replace it
        assert admin.must_change_password is True
        assert org.enabled_features == ["sequences", "automations", "ai_enrichment"]

        stages = PipelineStage.query.filter_by(organization_id=org.id).all()
        assert len(stages) >= 5

        # catalogue model: nothing is scheduled per customer
        assert SearchJob.query.filter_by(organization_id=org.id).count() == 0

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


def test_onboarded_customer_gets_leads_from_catalogue_history(app):
    """The whole point of onboarding: an active profile turns matching
    catalogue listings into leads on the next scheduler run."""
    from datetime import datetime, timezone

    from app.leads.models import Lead
    from app.listings.models import Listing, ListingRenovation
    from app.listings.services import materialize_pending_matches, rematch_profiles

    with app.app_context():
        now = datetime.now(timezone.utc)
        listing = Listing(
            source="oikotie",
            external_id="555",
            maakunta="Uusimaa",
            kaupunki="Espoo",
            osoite="Testikatu 1",
            taloyhtio="As Oy Valmis",
            analysis_status="done",
            first_seen_at=now,
            last_seen_at=now,
            published_at=now,
        )
        listing.renovations.append(
            ListingRenovation(remonttityyppi="Lukkoremontti / kulunvalvonta", confidence=90)
        )
        db.session.add(listing)
        actor = create_user("actor3@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        result = create_customer(
            actor=actor,
            organization_name="Katalogi Ready Oy",
            admin_email="katalogi@ready.fi",
            admin_password="securepassword1",
            admin_name=None,
            search_profile_name="Katalogiprofiili",
            regions=["Uusimaa"],
            remonttityyppi="Lukkoremontti / kulunvalvonta",
            is_active=True,
        )
        db.session.commit()
        org_id = result.organization.id

        assert rematch_profiles() == 1
        assert materialize_pending_matches() == 1
        lead = Lead.query.filter_by(organization_id=org_id).one()
        assert lead.source_ref == "oikotie-555"


def test_superadmin_customers_new_get_renders_form(client, app):
    """GET /admin/customers/new must render the onboarding template (not 500)."""
    _login_superadmin(client, app)

    response = client.get("/admin/customers/new")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Something went wrong" not in page
    assert "Luo uusi asiakas" in page
    assert 'name="organization_name"' in page
    assert 'name="remonttityyppi"' in page
    assert 'name="regions"' in page
    assert "Putkiremontti" in page
    assert "Uusimaa" in page


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
    assert "Hakuprofiili on aktiivinen" in page
    assert "fl_" not in page  # no API key is created or shown any more

    second_view = client.get("/admin/customers/result")
    assert second_view.status_code == 302


def test_superadmin_customers_list(app, client):
    with app.app_context():
        actor = create_user("sa-customers@test.com", "securepassword1", role="superadmin")
        actor.totp_enabled = True
        db.session.commit()
        create_customer(
            actor=actor,
            organization_name="Listaus Test Oy",
            admin_email="listaus@test.fi",
            admin_password="securepassword1",
            admin_name=None,
            search_profile_name="Lukkoremontti Uusimaa",
            regions=["Uusimaa"],
            remonttityyppi="Lukkoremontti / kulunvalvonta",
            is_active=True,
        )
        db.session.commit()

    client.post(
        "/auth/login",
        data={"email": "sa-customers@test.com", "password": "securepassword1"},
    )
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True

    response = client.get("/admin/customers")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Listaus Test Oy" in page
    assert "Lukkoremontti Uusimaa" in page
    assert "Asiakaslista" in page
    assert "Avaa" in page
    assert "Muokkaa" in page


def test_admin_cannot_access_customers_list(app, client):
    with app.app_context():
        from app.users.services import create_organization

        org = create_organization("Admin Org", "admin-org")
        db.session.flush()
        create_user(
            "admin-customers@test.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        db.session.commit()

    client.post(
        "/auth/login",
        data={"email": "admin-customers@test.com", "password": "securepassword1"},
    )
    response = client.get("/admin/customers")
    assert response.status_code == 403
