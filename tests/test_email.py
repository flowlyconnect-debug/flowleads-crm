import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

from app.core.permissions import TWO_FA_SESSION_KEY
from app.email.models import EmailLog, EmailTemplate
from app.email.seed import seed_system_email_templates
from app.email.services import (
    EmailService,
    EmailServiceError,
    TemplateService,
    handle_mailgun_webhook,
    verify_mailgun_webhook_signature,
)
from app.email.templates import render_template_text, validate_template_variables
from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService, get_default_stage
from app.users.models import AuditLog, Organization
from app.users.services import create_organization, create_user


def _setup_org(app, slug="email-org"):
    with app.app_context():
        org = create_organization("Email Org", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        user = create_user(
            f"user-{slug}@test.com",
            "securepassword1",
            role="user",
            organization_id=org.id,
        )
        other = create_organization("Other", f"{slug}-other")
        db.session.flush()
        other_user = create_user(
            f"other-{slug}@test.com",
            "securepassword1",
            role="user",
            organization_id=other.id,
        )
        db.session.commit()
        stage = get_default_stage(org.id)
        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "user_email": user.email,
            "other_org_id": other.id,
            "other_user_email": other_user.email,
            "stage_id": stage.id,
        }


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": "securepassword1"})


def _create_lead_with_email(app, org_id, stage_id, user_id, email="lead@test.com"):
    with app.app_context():
        lead = LeadService.create(
            {"email": email, "first_name": "Ada", "company": "Acme"},
            org_id,
            user_id,
        )
        db.session.commit()
        return lead.id


# --- Sending ---


@patch("app.email.services._mailgun_send")
def test_valid_email_send_creates_log_and_activity(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    ctx = _setup_org(app)
    lead_id = _create_lead_with_email(app, ctx["org_id"], ctx["stage_id"], ctx["admin_id"])

    with app.app_context():
        result = EmailService.send_to_lead(
            lead_id,
            ctx["admin_id"],
            "Hello",
            "<p>Hi</p>",
            None,
            organization_id=ctx["org_id"],
        )
        db.session.commit()
        assert result["success"] is True
        log = EmailLog.query.filter_by(lead_id=lead_id).one()
        assert log.status == "sent"
        assert log.mailgun_message_id == "<msg@test>"
        assert Activity.query.filter_by(lead_id=lead_id, type="email_sent").count() == 1
        assert AuditLog.query.filter_by(action="email_sent").count() == 1


@patch("app.email.services._mailgun_send")
def test_failed_send_creates_failed_log_no_activity(mock_send, app):
    mock_send.return_value = (False, None, "Provider error")
    ctx = _setup_org(app, "fail-org")
    lead_id = _create_lead_with_email(app, ctx["org_id"], ctx["stage_id"], ctx["admin_id"])

    with app.app_context():
        result = EmailService.send_to_lead(
            lead_id,
            ctx["admin_id"],
            "Hello",
            "<p>Hi</p>",
            None,
            organization_id=ctx["org_id"],
        )
        db.session.commit()
        assert result["success"] is False
        log = EmailLog.query.filter_by(lead_id=lead_id).one()
        assert log.status == "failed"
        assert Activity.query.filter_by(lead_id=lead_id, type="email_sent").count() == 0
        assert AuditLog.query.filter_by(action="email_failed").count() == 1


@patch("app.email.services._mailgun_send")
def test_failed_send_does_not_crash_route(mock_send, client, app):
    mock_send.return_value = (False, None, "Provider error")
    ctx = _setup_org(app, "route-fail")
    lead_id = _create_lead_with_email(app, ctx["org_id"], ctx["stage_id"], ctx["admin_id"])
    _login(client, ctx["admin_email"])

    response = client.post(
        f"/leads/{lead_id}/email/send",
        data={
            "subject": "Test",
            "body_html": "<p>Hi</p>",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert EmailLog.query.filter_by(lead_id=lead_id, status="failed").count() == 1


def test_lead_without_email_cannot_send(app):
    ctx = _setup_org(app, "no-email")
    with app.app_context():
        lead = LeadService.create(
            {"company": "No Email Co", "first_name": "Bob"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        with pytest.raises(EmailServiceError) as exc:
            EmailService.send_to_lead(
                lead.id,
                ctx["admin_id"],
                "Hi",
                "<p>x</p>",
                None,
                organization_id=ctx["org_id"],
            )
        assert exc.value.code == "no_email"


# --- Templates ---


def test_template_variables_substituted(app):
    ctx = {"first_name": "Ada", "last_name": "Lovelace", "company": "Acme", "sender_name": "Sam", "ai_summary": "AI"}
    out = render_template_text("Hi {{first_name}} at {{company}} — {{sender_name}}", ctx)
    assert "Ada" in out and "Acme" in out and "Sam" in out


def test_missing_variables_use_fallback(app):
    out = render_template_text("Hi {{first_name}}, {{company}}", {})
    assert "there" in out


def test_unknown_variables_rejected(app):
    ok, err = validate_template_variables("Hello {{unknown_var}}")
    assert not ok
    assert "unknown_var" in err


def test_system_templates_seeded_idempotently(app):
    with app.app_context():
        seed_system_email_templates()
        db.session.commit()
        count1 = EmailTemplate.query.filter_by(organization_id=None).count()
        seed_system_email_templates()
        db.session.commit()
        count2 = EmailTemplate.query.filter_by(organization_id=None).count()
        assert count1 >= 3
        assert count1 == count2


def test_organization_template_not_visible_cross_tenant(app):
    ctx = _setup_org(app, "tpl-x")
    with app.app_context():
        tpl = TemplateService.create_template(
            ctx["org_id"],
            ctx["admin_id"],
            name="Private",
            subject_template="Hi",
            body_html_template="<p>x</p>",
        )
        db.session.commit()
        with pytest.raises(EmailServiceError):
            TemplateService.get_template(tpl.id, ctx["other_org_id"])


# --- History ---


@patch("app.email.services._mailgun_send")
def test_email_history_visible(mock_send, client, app):
    mock_send.return_value = (True, "<id@x>", None)
    ctx = _setup_org(app, "hist")
    lead_id = _create_lead_with_email(app, ctx["org_id"], ctx["stage_id"], ctx["admin_id"])
    _login(client, ctx["admin_email"])
    client.post(
        f"/leads/{lead_id}/email/send",
        data={"subject": "Hist", "body_html": "<p>Body</p>"},
        follow_redirects=True,
    )
    response = client.get(f"/leads/{lead_id}/email/history")
    assert response.status_code == 200
    assert b"Hist" in response.data


@patch("app.email.services._mailgun_send")
def test_cross_tenant_cannot_view_history(mock_send, client, app):
    mock_send.return_value = (True, "<id@y>", None)
    ctx = _setup_org(app, "cross-hist")
    lead_id = _create_lead_with_email(app, ctx["org_id"], ctx["stage_id"], ctx["admin_id"])
    _login(client, ctx["admin_email"])
    client.post(
        f"/leads/{lead_id}/email/send",
        data={"subject": "Secret", "body_html": "<p>x</p>"},
        follow_redirects=True,
    )
    with app.app_context():
        log_id = EmailLog.query.filter_by(lead_id=lead_id).first().id

    client.get("/auth/logout", follow_redirects=True)
    _login(client, ctx["other_user_email"])
    response = client.get(f"/leads/{lead_id}/email/history/{log_id}")
    assert response.status_code == 404
    assert b"Secret" not in response.data


# --- Settings ---


def test_admin_can_update_email_settings(client, app):
    ctx = _setup_org(app, "set-org")
    _login(client, ctx["admin_email"])
    response = client.post(
        "/settings/email",
        data={
            "email_from_name": "Acme Sales",
            "email_from_email": "sales@acme.com",
            "mailgun_domain": "mg.acme.com",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        org = db.session.get(Organization, ctx["org_id"])
        assert org.email_from_name == "Acme Sales"
        assert org.email_from_email == "sales@acme.com"
        assert AuditLog.query.filter_by(action="email_settings_updated").count() == 1


def test_normal_user_cannot_update_email_settings(client, app):
    ctx = _setup_org(app, "set-user")
    _login(client, ctx["user_email"])
    response = client.post(
        "/settings/email",
        data={"email_from_name": "Hack", "email_from_email": "x@y.com"},
    )
    assert response.status_code == 403


def test_invalid_from_email_rejected(client, app):
    ctx = _setup_org(app, "set-bad")
    _login(client, ctx["admin_email"])
    with app.app_context():
        org = db.session.get(Organization, ctx["org_id"])
        before = org.email_from_email
    response = client.post(
        "/settings/email",
        data={"email_from_name": "X", "email_from_email": "not-an-email"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        org = db.session.get(Organization, ctx["org_id"])
        assert org.email_from_email == before


# --- Webhook ---


def test_webhook_valid_signature_updates_status(app, client):
    ctx = _setup_org(app, "wh")
    with app.app_context():
        org = db.session.get(Organization, ctx["org_id"])
        lead = LeadService.create({"email": "l@t.com", "company": "C"}, ctx["org_id"], ctx["admin_id"])
        log = EmailLog(
            lead_id=lead.id,
            user_id=ctx["admin_id"],
            organization_id=ctx["org_id"],
            subject="S",
            body_html="<p>x</p>",
            status="sent",
            mailgun_message_id="<wh@test>",
        )
        db.session.add(log)
        db.session.commit()
        log_id = log.id
        lead_id = lead.id

    signing_key = "test-signing-key"
    client.application.config["MAILGUN_WEBHOOK_SIGNING_KEY"] = signing_key
    timestamp = "1529006854"
    token = "abc123"
    signature = hmac.new(
        signing_key.encode(),
        f"{timestamp}{token}".encode(),
        hashlib.sha256,
    ).hexdigest()

    payload = {
        "event": "opened",
        "message": {"headers": {"message-id": "<wh@test>"}},
    }
    response = client.post(
        "/api/webhooks/mailgun",
        data={
            "timestamp": timestamp,
            "token": token,
            "signature": signature,
            "event-data": json.dumps(payload),
        },
    )
    assert response.status_code == 200
    with app.app_context():
        log = db.session.get(EmailLog, log_id)
        assert log.status == "opened"
        assert Activity.query.filter_by(type="email_opened", lead_id=lead_id).count() == 1


def test_webhook_invalid_signature_rejected(client, app):
    client.application.config["MAILGUN_WEBHOOK_SIGNING_KEY"] = "key"
    response = client.post(
        "/api/webhooks/mailgun",
        data={"timestamp": "1", "token": "t", "signature": "bad", "event-data": "{}"},
    )
    assert response.status_code == 403


def test_webhook_missing_signing_key_returns_503(client, app):
    client.application.config["MAILGUN_WEBHOOK_SIGNING_KEY"] = None
    response = client.post(
        "/api/webhooks/mailgun",
        data={"timestamp": "1", "token": "t", "signature": "x"},
    )
    assert response.status_code == 503


# --- Superadmin ---


def test_superadmin_email_logs_require_2fa(client, app):
    with app.app_context():
        sa = create_user("sa-email@test.com", "securepassword1", role="superadmin")
        sa.totp_enabled = True
        db.session.commit()

    client.post("/auth/login", data={"email": "sa-email@test.com", "password": "securepassword1"})
    response = client.get("/admin/email/logs")
    assert response.status_code == 302

    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    response = client.get("/admin/email/logs")
    assert response.status_code == 200


@patch("app.email.services._mailgun_send")
def test_superadmin_test_email(mock_send, client, app):
    mock_send.return_value = (True, "<test@x>", None)
    with app.app_context():
        sa = create_user("sa-test@test.com", "securepassword1", role="superadmin")
        sa.totp_enabled = True
        db.session.commit()

    client.post("/auth/login", data={"email": "sa-test@test.com", "password": "securepassword1"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    response = client.post("/admin/email/test", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert AuditLog.query.filter_by(action="email_test_sent").count() >= 1


def test_verify_mailgun_signature_helper(app):
    with app.app_context():
        app.config["MAILGUN_WEBHOOK_SIGNING_KEY"] = "secret"
        ts, tok = "123", "abc"
        sig = hmac.new(b"secret", f"{ts}{tok}".encode(), hashlib.sha256).hexdigest()
        assert verify_mailgun_webhook_signature(ts, tok, sig)
