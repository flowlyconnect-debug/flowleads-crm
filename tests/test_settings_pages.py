"""The settings menu after the review of 11/2026: merged pages and the
items each role sees."""

from __future__ import annotations

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.users.services import create_organization, create_user


def _setup(app, slug: str):
    with app.app_context():
        org = create_organization(f"Asetus {slug}", f"asetus-{slug}")
        db.session.flush()
        admin = create_user(f"admin-{slug}@test.fi", "securepassword1", role="admin", organization_id=org.id)
        admin.totp_enabled = True
        create_user(f"myyja-{slug}@test.fi", "securepassword1", role="user", organization_id=org.id)
        sa = create_user(f"sa-{slug}@test.fi", "securepassword1", role="superadmin")
        sa.totp_enabled = True
        db.session.commit()
        return org.id


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": "securepassword1"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True


def test_both_daily_digests_are_set_on_the_lead_settings_page(client, app):
    org_id = _setup(app, "digest")
    _login(client, "admin-digest@test.fi")
    page = client.get("/settings/leads").get_data(as_text=True)
    assert "Liidit ja koosteet" in page
    assert 'name="targets_digest_enabled"' in page
    assert 'name="daily_digest_enabled"' in page

    client.post("/settings/leads", data={"daily_digest_enabled": "y"})
    from app.streams.models import OrgLeadSettings
    from app.tasks.settings import get_organization_settings

    with app.app_context():
        assert get_organization_settings(org_id).targets_digest_enabled is False
        assert OrgLeadSettings.query.filter_by(organization_id=org_id).one().daily_digest_enabled is True

    client.post("/settings/leads", data={"targets_digest_enabled": "y"})
    with app.app_context():
        assert get_organization_settings(org_id).targets_digest_enabled is True
        assert OrgLeadSettings.query.filter_by(organization_id=org_id).one().daily_digest_enabled is False


def test_admin_finds_sender_and_templates_on_one_email_page(client, app):
    _setup(app, "email")
    _login(client, "admin-email@test.fi")
    response = client.get("/settings/email-templates")
    assert response.status_code == 302
    assert response.location.endswith("/settings/email#mallit")
    page = client.get("/settings/email").get_data(as_text=True)
    assert 'id="mallit"' in page
    assert "Seurantaviesti" in page  # a system template people can use
    assert "/settings/email-templates/new" in page

    client.post(
        "/settings/email-templates",
        data={"name": "Esittely", "subject_template": "Hei", "body_html_template": "<p>Hei</p>"},
    )
    assert "Esittely" in client.get("/settings/email").get_data(as_text=True)


def test_salesperson_keeps_the_read_only_template_list(client, app):
    _setup(app, "myyja")
    _login(client, "myyja-myyja@test.fi")
    response = client.get("/settings/email-templates")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Seurantaviesti" in page
    assert "/settings/email-templates/new" not in page
    assert client.get("/settings/email").status_code == 403


def test_settings_menu_of_a_customer_admin(client, app):
    app.config.update(GOOGLE_CLIENT_ID="", MICROSOFT_CLIENT_ID="")
    _setup(app, "nav")
    _login(client, "admin-nav@test.fi")
    page = client.get("/settings/team").get_data(as_text=True)
    nav = page[page.index('class="settings-nav"'):page.index("</nav>", page.index('class="settings-nav"'))]
    for label in ("Käyttäjät", "Urakat ja kohdehaku", "Liidit ja koosteet", "Sähköposti", "Tarjoukset", "Tilaus"):
        assert label in nav, label
    assert "Sähköpostimallit" not in nav  # part of the Sähköposti page
    assert "Kalenteri" not in nav  # Google/Outlook are not configured here
    assert 'aria-current="page"' in nav


def test_calendar_shows_up_when_a_provider_is_configured(client, app):
    app.config.update(
        GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret", GOOGLE_REDIRECT_URI="https://x.fi/cb"
    )
    _setup(app, "cal")
    _login(client, "admin-cal@test.fi")
    page = client.get("/settings/team").get_data(as_text=True)
    assert "/settings/calendar" in page


def test_salesperson_settings_home_without_calendar(client, app):
    app.config.update(GOOGLE_CLIENT_ID="", MICROSOFT_CLIENT_ID="")
    _setup(app, "home")
    _login(client, "myyja-home@test.fi")
    page = client.get("/dashboard", follow_redirects=True).get_data(as_text=True)
    assert 'href="/settings/email-templates"' in page


def test_superadmin_overview_is_one_reports_page(client, app):
    org_id = _setup(app, "sa")
    _login(client, "sa-sa@test.fi")
    assert client.get("/admin/dashboard").location.endswith("/admin/reports")
    page = client.get("/admin/reports").get_data(as_text=True)
    assert "Käyttäjiä" in page
    assert "Hallintapaneeli" not in page
    org_page = client.get(f"/settings/team?organization_id={org_id}").get_data(as_text=True)
    assert "Liidit ja koosteet" in org_page
    assert "Järjestelmäraportit" in org_page
