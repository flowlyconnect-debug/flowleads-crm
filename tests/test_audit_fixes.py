"""Regression tests for the defects found in the full-system audit (11/2026).

Each test pins one thing that was broken for a real user: a button that did
nothing, a page that answered 400/500, a security hole, a wrong number.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from app.core.permissions import TWO_FA_SESSION_KEY
from app.email.models import EmailLog, EmailTemplate
from app.extensions import db
from app.leads.models import Lead, PipelineStage
from app.leads.services import get_default_stage
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"


def _org(slug: str, role: str = "admin"):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"{role}-{slug}@test.com", PASSWORD, role=role, organization_id=org.id)
    user.totp_enabled = True
    db.session.commit()
    return org, user


def _superadmin(email: str):
    user = create_user(email, PASSWORD, role="superadmin")
    user.totp_enabled = True
    db.session.commit()
    return user


def _lead(org_id: int, **fields) -> Lead:
    lead = Lead(
        organization_id=org_id,
        stage_id=get_default_stage(org_id).id,
        status="active",
        source="manual",
        **fields,
    )
    db.session.add(lead)
    db.session.commit()
    return lead


def _login(client, email: str, **kwargs):
    response = client.post("/auth/login", data={"email": email, "password": PASSWORD}, **kwargs)
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True  # admins: second factor passed
    return response


# --- security ----------------------------------------------------------------


def test_login_does_not_redirect_to_another_site(client, app):
    with app.app_context():
        _, user = _org("redir")
        email = user.email
    response = client.post(
        "/auth/login?next=https://evil.example/phish", data={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 302
    assert "evil.example" not in response.headers["Location"]


def test_login_keeps_a_local_next_target(client, app):
    with app.app_context():
        _, user = _org("redir2", role="user")
        email = user.email
    response = client.post("/auth/login?next=/tasks", data={"email": email, "password": PASSWORD})
    assert response.headers["Location"].endswith("/tasks")


def test_proposal_pdf_does_not_fetch_local_files():
    import pytest

    from app.proposals.pdf import _safe_url_fetcher

    with pytest.raises(ValueError):
        _safe_url_fetcher("file:///etc/hostname")
    with pytest.raises(ValueError):
        _safe_url_fetcher("http://169.254.169.254/latest/meta-data")


# --- lead page -----------------------------------------------------------------


def test_archive_button_on_lead_page_archives(client, app):
    with app.app_context():
        org, user = _org("archive")
        lead_id = _lead(org.id, company="As Oy Arkisto").id
        email = user.email
    _login(client, email)
    response = client.post(f"/leads/{lead_id}", data={"_method": "DELETE"})
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Lead, lead_id).status == "archived"


def test_lead_can_be_marked_lost_from_the_stage_form(client, app):
    with app.app_context():
        org, user = _org("lost")
        lead_id = _lead(org.id, company="As Oy Hävitty").id
        lost_stage_id = PipelineStage.query.filter_by(organization_id=org.id, name="Hävitty").one().id
        email = user.email
    _login(client, email)
    response = client.post(
        f"/leads/{lead_id}/stage", data={"stage_id": lost_stage_id, "lost_reason": "no_budget"}
    )
    assert response.status_code == 302
    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert (lead.status, lead.lost_reason) == ("lost", "no_budget")


def test_bulk_move_to_lost_works_without_a_json_error_page(client, app):
    with app.app_context():
        org, user = _org("bulklost")
        lead_id = _lead(org.id, company="As Oy Massa").id
        lost_stage_id = PipelineStage.query.filter_by(organization_id=org.id, name="Hävitty").one().id
        email = user.email
    _login(client, email)
    response = client.post(
        "/leads/bulk",
        data={"action": "change_stage", "lead_ids": str(lead_id), "stage_id": lost_stage_id},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Lead, lead_id).status == "lost"


def test_bad_filter_values_do_not_crash_lead_pages(client, app):
    with app.app_context():
        _, user = _org("params")
        email = user.email
    _login(client, email)
    for url in (
        "/leads?score_min=abc&score_max=x",
        "/leads/pipeline?assigned_to=abc&score_min=abc",
        "/leads/export?score_min=x",
    ):
        assert client.get(url).status_code == 200, url


def test_lead_export_opens_in_finnish_excel(client, app):
    with app.app_context():
        org, user = _org("csv")
        _lead(org.id, company="As Oy Äänekoski", email="aa@example.fi")
        email = user.email
    _login(client, email)
    body = client.get("/leads/export").get_data(as_text=True)
    assert body.startswith("﻿ID;Etunimi;Sukunimi;")
    assert "As Oy Äänekoski" in body


def test_same_lead_is_not_created_twice_from_the_form(client, app):
    with app.app_context():
        org, user = _org("dup")
        _lead(org.id, company="As Oy Tupla", email="pj@tupla.fi")
        email, org_id = user.email, org.id
    _login(client, email)
    response = client.post("/leads", data={"company": "as oy tupla", "email": "PJ@tupla.fi", "source": "manual"})
    assert response.status_code == 400
    with app.app_context():
        assert Lead.query.filter_by(organization_id=org_id).count() == 1


# --- pages that used to answer 500 / 400 -------------------------------------------


def test_automation_form_renders(client, app):
    with app.app_context():
        _, user = _org("autoform")
        email = user.email
    _login(client, email)
    response = client.get("/automations/new")
    assert response.status_code == 200
    assert "{{lead.id}}" in response.get_data(as_text=True)  # the literal placeholder, not evaluated


def test_superadmin_is_sent_to_the_organization_picker_not_a_400(client, app):
    with app.app_context():
        _superadmin("sa-picker@test.com")
    _login(client, "sa-picker@test.com")
    for url in ("/leads", "/tasks", "/calendar", "/settings/webhooks", "/settings/team"):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response.headers["Location"].endswith("/dashboard"), url
    assert client.get("/api/notifications").status_code == 200


def test_superadmin_organization_choice_is_remembered(client, app):
    with app.app_context():
        org, _ = _org("remember")
        _superadmin("sa-remember@test.com")
        org_id = org.id
    _login(client, "sa-remember@test.com")
    assert client.get(f"/leads?organization_id={org_id}").status_code == 200
    # a link that drops the parameter stays in the chosen organization
    assert client.get("/tasks").status_code == 200
    assert client.get("/settings/privacy").status_code == 200
    # the picker forgets it again
    client.get("/dashboard")
    assert client.get("/tasks").status_code == 302


def test_duplicate_email_template_name_is_a_message_not_a_500(client, app):
    with app.app_context():
        _, user = _org("tpldup")
        email = user.email
    _login(client, email)
    data = {"name": "Esittely", "subject_template": "Hei", "body_html_template": "<p>Hei</p>"}
    assert client.post("/settings/email-templates", data=data).status_code == 302
    response = client.post("/settings/email-templates", data=data, follow_redirects=True)
    assert response.status_code == 200
    assert "Samanniminen" in response.get_data(as_text=True)


def test_editing_an_email_template_does_not_create_a_copy(client, app):
    with app.app_context():
        org, user = _org("tpledit")
        email, org_id = user.email, org.id
    _login(client, email)
    client.post(
        "/settings/email-templates",
        data={"name": "Esittely", "subject_template": "Hei", "body_html_template": "<p>Hei</p>"},
    )
    with app.app_context():
        template_id = EmailTemplate.query.filter_by(organization_id=org_id).one().id
    page = client.get(f"/settings/email-templates/{template_id}/edit").get_data(as_text=True)
    assert f'action="/settings/email-templates/{template_id}/edit"' in page
    client.post(
        f"/settings/email-templates/{template_id}/edit",
        data={"name": "Esittely", "subject_template": "Uusi aihe", "body_html_template": "<p>Hei</p>"},
    )
    with app.app_context():
        templates = EmailTemplate.query.filter_by(organization_id=org_id).all()
        assert [t.subject_template for t in templates] == ["Uusi aihe"]


def test_system_template_preview_link_survives_the_request(client, app):
    with app.app_context():
        EmailTemplate.query.delete()
        db.session.commit()
        _, user = _org("tplprev")
        email = user.email
    _login(client, email)
    client.get("/settings/email-templates")  # seeds the system templates
    with app.app_context():
        template = EmailTemplate.query.filter_by(organization_id=None, name="Seurantaviesti").one()
        template_id = template.id
    assert client.get(f"/settings/email-templates/{template_id}/preview").status_code == 200


# --- email ------------------------------------------------------------------------


def test_privacy_footer_does_not_break_sending(app):
    from app.email.services import _append_privacy_footer
    from app.gdpr.settings import get_privacy_settings

    with app.app_context():
        org, _ = _org("footer")
        settings = get_privacy_settings(org.id)
        settings.privacy_policy_url = "https://example.fi/tietosuoja"
        db.session.commit()
        html_body, text_body = _append_privacy_footer("<p>Hei</p>", "Hei", organization_id=org.id)
        assert "https://example.fi/tietosuoja" in html_body
        assert "Tietosuojaseloste" in text_body


def test_unsubscribe_link_from_the_scheduler_uses_the_public_address(app):
    from app.sequences.services import _unsubscribe_url

    with app.app_context():
        app.config["APP_BASE_URL"] = "https://crm.example.fi/"
        url = _unsubscribe_url(1, 2)
    assert url.startswith("https://crm.example.fi/unsubscribe?token=")


def test_mailgun_json_webhook_updates_the_email_status(client, app):
    with app.app_context():
        org, user = _org("mgjson")
        lead = _lead(org.id, company="As Oy Webhook", email="pj@webhook.fi")
        log = EmailLog(
            organization_id=org.id,
            lead_id=lead.id,
            user_id=user.id,
            subject="Hei",
            status="sent",
            mailgun_message_id="<20261111.abc@mg.example.fi>",
            sent_at=datetime.now(timezone.utc),
        )
        db.session.add(log)
        db.session.commit()
        log_id = log.id
        key = app.config["MAILGUN_WEBHOOK_SIGNING_KEY"]

    timestamp, token = "1762862400", "a" * 50
    signature = hmac.new(key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256).hexdigest()
    response = client.post(
        "/api/webhooks/mailgun",
        json={
            "signature": {"timestamp": timestamp, "token": token, "signature": signature},
            # Mailgun sends the bare id; the stored one has angle brackets
            "event-data": {
                "event": "delivered",
                "message": {"headers": {"message-id": "20261111.abc@mg.example.fi"}},
            },
        },
    )
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(EmailLog, log_id).status == "delivered"

    bad = client.post(
        "/api/webhooks/mailgun",
        json={"signature": {"timestamp": timestamp, "token": token, "signature": "0" * 64}, "event-data": {}},
    )
    assert bad.status_code == 403


def test_template_without_first_name_reads_naturally(app):
    from app.email.templates import build_template_context, render_template_text

    with app.app_context():
        lead = Lead(company="As Oy Nimetön")
        text = render_template_text("Hei {{first_name}},", build_template_context(lead, "Aino"))
    assert text == "Hei,"


# --- GDPR ---------------------------------------------------------------------------


def test_gdpr_password_check_accepts_the_right_password(app):
    import pytest

    from app.gdpr.services import GDPRService, GDPRServiceError

    with app.app_context():
        _, user = _org("gdprpw")
        GDPRService.verify_password(user, PASSWORD)
        with pytest.raises(GDPRServiceError):
            GDPRService.verify_password(user, "wrong-password-123")


def test_anonymising_clears_contact_details_and_works_without_a_user(app):
    from app.gdpr.services import GDPRService

    with app.app_context():
        org, _ = _org("gdpranon")
        lead = _lead(
            org.id,
            company="As Oy Yksityisyys",
            first_name="Matti",
            email="matti@example.fi",
            title="Isännöitsijä",
            ai_contact_info={"isannoitsija_puh": "0401234567"},
        )
        # the monthly retention job has no user: this used to violate a foreign key
        GDPRService.anonymize_lead(lead.id, None, org.id, reason="retention_policy")
        db.session.commit()
        lead = db.session.get(Lead, lead.id)
        assert lead.is_anonymized
        assert lead.title is None and lead.ai_contact_info is None


# --- catalogue / onboarding ----------------------------------------------------------


def test_import_accepts_comma_separated_renovation_types(app):
    from app.listings.import_services import _renovations

    with app.app_context():
        parsed, error = _renovations({"remonttityyppi": "Putkiremontti, Julkisivuremontti"})
    assert error is None
    assert [r["remonttityyppi"] for r in parsed] == ["Putkiremontti", "Julkisivuremontti"]


def test_malformed_manager_email_does_not_sink_the_target(app):
    from app.listings.services import _valid_email_or_none

    with app.app_context():
        assert _valid_email_or_none("info@firma") is None
        assert _valid_email_or_none("matti(at)x.fi @") is None
        assert _valid_email_or_none(" Isannointi@Kalliola.fi ") == "isannointi@kalliola.fi"


def test_invite_is_validated_before_an_organization_is_created(app):
    import pytest

    from app.admin.invite_services import InviteServiceError, create_invite
    from app.users.models import Organization

    with app.app_context():
        _, existing = _org("invite")
        actor = _superadmin("sa-invite@test.com")
        before = Organization.query.count()
        cases = (
            {"admin_email": "not-an-email", "remonttityyppi": "Putkiremontti"},
            {"admin_email": existing.email, "remonttityyppi": "Putkiremontti"},
            {"admin_email": "uusi@example.fi", "remonttityyppi": "EI_OLE_OLEMASSA"},
        )
        for case in cases:
            with pytest.raises(InviteServiceError):
                create_invite(
                    actor=actor,
                    organization_name="Uusi Asiakas Oy",
                    search_profile_name="Profiili",
                    **case,
                )
            db.session.rollback()
        assert Organization.query.count() == before


# --- presentation ---------------------------------------------------------------------


def test_timestamps_are_shown_in_finnish_time(app):
    from app import helsinki_to_utc, to_helsinki

    winter = to_helsinki(datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc))
    summer = to_helsinki(datetime(2026, 7, 1, 10, 0))  # naive = UTC, as SQLite returns it
    assert (winter.hour, summer.hour) == (12, 13)
    assert helsinki_to_utc(datetime(2026, 11, 11, 10, 0)).hour == 8


def test_ui_messages_are_finnish():
    from app.core.messages_fi import fi_message

    assert fi_message("Lead not found.") == "Liidiä ei löytynyt."
    assert fi_message("Backup created: db.sql.gz") == "Varmuuskopio luotu: db.sql.gz"
    assert fi_message("Hakuprofiili tallennettu.") == "Hakuprofiili tallennettu."
