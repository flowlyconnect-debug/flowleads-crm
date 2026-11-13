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


# --- what a customer admin must not reach -------------------------------------------


def test_proposal_header_html_cannot_run_scripts(client, app):
    from app.core.html_sanitize import sanitize_html

    dirty = (
        '<p onclick="x()">Hei <strong>Oy</strong></p><script>alert(1)</script>'
        '<img src="javascript:alert(1)" onerror="x()"><a href="javascript:x()">a</a>'
        '<a href="https://yritys.fi">b</a><style>body{}</style>'
    )
    clean = sanitize_html(dirty)
    assert "<script" not in clean and "alert" not in clean
    assert "onclick" not in clean and "onerror" not in clean and "javascript:" not in clean
    assert "<strong>Oy</strong>" in clean
    assert 'href="https://yritys.fi"' in clean


def test_proposal_preview_has_no_script_policy(client, app):
    org_id = _setup(app, "prop")
    from app.leads.models import Lead
    from app.proposals.models import Proposal
    from app.proposals.services import ProposalService
    from app.users.models import User

    with app.app_context():
        admin = User.query.filter_by(email="admin-prop@test.fi").one()
        ProposalService.save_template(org_id, {"header_html": "<script>alert(1)</script><b>Otsikko</b>"}, admin.id)
        from app.leads.services import get_default_stage

        stage = get_default_stage(org_id)
        lead = Lead(organization_id=org_id, company="As Oy Testi", email="t@t.fi", stage_id=stage.id)
        db.session.add(lead)
        db.session.flush()
        proposal = Proposal(
            organization_id=org_id, lead_id=lead.id, title="T", reference_number="FLW-1", created_by=admin.id
        )
        db.session.add(proposal)
        db.session.commit()
        proposal_id = proposal.id
    _login(client, "admin-prop@test.fi")
    response = client.get(f"/proposals/{proposal_id}/preview")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "<b>Otsikko</b>" in body
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]


def test_customer_does_not_see_catalogue_size_or_preview_addresses(client, app):
    _setup(app, "cat")
    _login(client, "admin-cat@test.fi")
    page = client.get("/settings/search-profiles?id=new").get_data(as_text=True)
    assert "Kohdekatalogissa" not in page
    response = client.post(
        "/settings/search-profiles/preview",
        data={"name": "x", "remonttityypit": "Putkiremontti", "region_mode": "all_finland"},
    )
    data = response.get_json()
    assert "count" in data
    assert "sample" not in data


def test_customer_cannot_preview_platform_notification_templates(client, app):
    _setup(app, "intern")
    _login(client, "admin-intern@test.fi")
    client.get("/settings/email")  # seeds the templates
    from app.email.models import EmailTemplate
    from app.email.seed import INTERNAL_TEMPLATE_NAMES

    with app.app_context():
        internal = EmailTemplate.query.filter(
            EmailTemplate.organization_id.is_(None), EmailTemplate.name.in_(list(INTERNAL_TEMPLATE_NAMES))
        ).first()
        assert internal is not None
        internal_id = internal.id
    assert client.get(f"/settings/email-templates/{internal_id}/preview").status_code == 404
