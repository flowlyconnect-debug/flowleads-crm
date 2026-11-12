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
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True  # admins must pass 2FA before any page


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


@patch("app.email.services._mailgun_send")
def test_daily_send_limit_blocks_further_sends_for_organization(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    ctx = _setup_org(app, "rate-limited-org")
    lead_id = _create_lead_with_email(app, ctx["org_id"], ctx["stage_id"], ctx["admin_id"])

    with app.app_context():
        app.config["EMAIL_DAILY_LIMIT_PER_ORG"] = 2
        for _ in range(2):
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

        with pytest.raises(EmailServiceError) as exc:
            EmailService.send_to_lead(
                lead_id,
                ctx["admin_id"],
                "Hello",
                "<p>Hi</p>",
                None,
                organization_id=ctx["org_id"],
            )
        assert exc.value.code == "rate_limited"


@patch("app.email.services._mailgun_send")
def test_daily_send_limit_does_not_affect_other_organizations(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    ctx = _setup_org(app, "rate-limited-org-2")
    lead_id = _create_lead_with_email(app, ctx["org_id"], ctx["stage_id"], ctx["admin_id"])
    other_lead_id = _create_lead_with_email(
        app, ctx["other_org_id"], ctx["stage_id"], ctx["admin_id"], email="other-lead@test.com"
    )

    with app.app_context():
        app.config["EMAIL_DAILY_LIMIT_PER_ORG"] = 1
        EmailService.send_to_lead(
            lead_id, ctx["admin_id"], "Hello", "<p>Hi</p>", None, organization_id=ctx["org_id"]
        )
        db.session.commit()

        result = EmailService.send_to_lead(
            other_lead_id,
            ctx["admin_id"],
            "Hello",
            "<p>Hi</p>",
            None,
            organization_id=ctx["other_org_id"],
        )
        db.session.commit()
        assert result["success"] is True


@patch("app.email.services._mailgun_send")
def test_daily_send_limit_disabled_when_zero(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    ctx = _setup_org(app, "rate-limit-disabled-org")
    lead_id = _create_lead_with_email(app, ctx["org_id"], ctx["stage_id"], ctx["admin_id"])

    with app.app_context():
        app.config["EMAIL_DAILY_LIMIT_PER_ORG"] = 0
        for _ in range(3):
            result = EmailService.send_to_lead(
                lead_id, ctx["admin_id"], "Hello", "<p>Hi</p>", None, organization_id=ctx["org_id"]
            )
            db.session.commit()
            assert result["success"] is True


# --- Templates ---


def test_template_variables_substituted(app):
    ctx = {"first_name": "Ada", "last_name": "Lovelace", "company": "Acme", "sender_name": "Sam", "ai_summary": "AI"}
    out = render_template_text("Hi {{first_name}} at {{company}} — {{sender_name}}", ctx)
    assert "Ada" in out and "Acme" in out and "Sam" in out


def test_missing_variables_use_fallback(app):
    out = render_template_text("Hei {{first_name}}, {{company}}", {})
    assert out == "Hei, "


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


# --- Per-organization verified sending domain ---


def test_extract_and_check_dns_records_pure_helpers():
    from app.email.mailgun_domains import extract_sending_dns_records, is_domain_fully_verified

    record = {
        "domain": {
            "sending_dns_records": [
                {"record_type": "TXT", "name": "mg.example.com", "value": "v=spf1...", "valid": "valid"},
                {"record_type": "TXT", "name": "k1._domainkey.mg.example.com", "value": "k=rsa...", "valid": "unknown"},
            ]
        }
    }
    records = extract_sending_dns_records(record)
    assert len(records) == 2
    assert records[0]["record_type"] == "TXT"
    assert not is_domain_fully_verified(record)

    record["domain"]["sending_dns_records"][1]["valid"] = "valid"
    assert is_domain_fully_verified(record)


def test_is_domain_fully_verified_false_when_no_records():
    from app.email.mailgun_domains import is_domain_fully_verified

    assert is_domain_fully_verified({"domain": {"sending_dns_records": []}}) is False


@patch("app.email.mailgun_domains.add_domain")
def test_register_organization_domain_returns_dns_records(mock_add, app):
    from app.email.services import register_organization_domain

    mock_add.return_value = {
        "domain": {
            "sending_dns_records": [
                {"record_type": "TXT", "name": "mg.example.com", "value": "v=spf1...", "valid": "unknown"},
            ]
        }
    }
    ctx = _setup_org(app, "domain-register")
    with app.app_context():
        org = Organization.query.get(ctx["org_id"])
        org.mailgun_domain = "mg.example.com"
        db.session.commit()

        records = register_organization_domain(org)
        mock_add.assert_called_once_with("mg.example.com")
        assert records[0]["record_type"] == "TXT"
        assert AuditLog.query.filter_by(action="mailgun_domain_registered").count() == 1


@patch("app.email.mailgun_domains.trigger_verification")
@patch("app.email.mailgun_domains.get_domain_status")
def test_check_domain_status_sets_verified_at_when_valid(mock_status, mock_verify, app):
    from app.email.services import check_organization_domain_status

    mock_status.return_value = {
        "domain": {
            "sending_dns_records": [
                {"record_type": "TXT", "name": "mg.example.com", "value": "v=spf1...", "valid": "valid"},
            ]
        }
    }
    ctx = _setup_org(app, "domain-verify")
    with app.app_context():
        org = Organization.query.get(ctx["org_id"])
        org.mailgun_domain = "mg.example.com"
        db.session.commit()

        check_organization_domain_status(org)
        assert org.mailgun_domain_verified_at is not None
        assert AuditLog.query.filter_by(action="mailgun_domain_verified").count() == 1


@patch("app.email.mailgun_domains.trigger_verification")
@patch("app.email.mailgun_domains.get_domain_status")
def test_check_domain_status_clears_verified_at_when_invalid(mock_status, mock_verify, app):
    from app.email.services import check_organization_domain_status

    mock_status.return_value = {
        "domain": {
            "sending_dns_records": [
                {"record_type": "TXT", "name": "mg.example.com", "value": "v=spf1...", "valid": "unknown"},
            ]
        }
    }
    ctx = _setup_org(app, "domain-verify-fail")
    with app.app_context():
        from datetime import datetime, timezone

        org = Organization.query.get(ctx["org_id"])
        org.mailgun_domain = "mg.example.com"
        org.mailgun_domain_verified_at = datetime.now(timezone.utc)
        db.session.commit()

        check_organization_domain_status(org)
        assert org.mailgun_domain_verified_at is None


def test_changing_domain_clears_verification(app):
    from app.email.services import update_organization_email_settings

    ctx = _setup_org(app, "domain-change")
    with app.app_context():
        from datetime import datetime, timezone

        org = Organization.query.get(ctx["org_id"])
        org.mailgun_domain = "mg.old.com"
        org.mailgun_domain_verified_at = datetime.now(timezone.utc)
        db.session.commit()

        update_organization_email_settings(
            org,
            email_from_name="Acme Sales",
            email_from_email="sales@acme.com",
            mailgun_domain="mg.new.com",
            user_id=ctx["admin_id"],
        )
        assert org.mailgun_domain_verified_at is None


@patch("app.email.services._mailgun_send")
def test_send_to_lead_uses_verified_domain(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    ctx = _setup_org(app, "domain-send-verified")
    lead_id = _create_lead_with_email(app, ctx["org_id"], ctx["stage_id"], ctx["admin_id"])

    with app.app_context():
        from datetime import datetime, timezone

        org = Organization.query.get(ctx["org_id"])
        org.mailgun_domain = "mg.verified.com"
        org.mailgun_domain_verified_at = datetime.now(timezone.utc)
        org.email_from_email = "sales@verified.com"
        db.session.commit()

        EmailService.send_to_lead(
            lead_id, ctx["admin_id"], "Subject", "<p>Hi</p>", None, organization_id=ctx["org_id"]
        )
        assert mock_send.call_args.kwargs["domain"] == "mg.verified.com"
        # the address is on the verified domain: it is the sender, no Reply-To
        assert mock_send.call_args.kwargs["from_email"] == "sales@verified.com"
        assert mock_send.call_args.kwargs["reply_to"] is None


@patch("app.email.services._mailgun_send")
def test_send_to_lead_falls_back_to_shared_domain_when_unverified(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    ctx = _setup_org(app, "domain-send-unverified")
    lead_id = _create_lead_with_email(app, ctx["org_id"], ctx["stage_id"], ctx["admin_id"])

    with app.app_context():
        org = Organization.query.get(ctx["org_id"])
        org.mailgun_domain = "mg.unverified.com"  # set but never verified
        org.email_from_email = "sales@unverified.com"
        db.session.commit()

        EmailService.send_to_lead(
            lead_id, ctx["admin_id"], "Subject", "<p>Hi</p>", None, organization_id=ctx["org_id"]
        )
        assert mock_send.call_args.kwargs["domain"] is None
        # an unverified address is never the sender: platform address + Reply-To
        assert mock_send.call_args.kwargs["from_email"] != "sales@unverified.com"
        assert mock_send.call_args.kwargs["reply_to"] == "sales@unverified.com"


def test_email_settings_404_for_nonexistent_organization(client, app):
    with app.app_context():
        create_user("sa-email-404@test.com", "securepassword1", role="superadmin")
        db.session.commit()

    client.post("/auth/login", data={"email": "sa-email-404@test.com", "password": "securepassword1"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    response = client.get("/settings/email?organization_id=999999")
    # an unknown organization sends the superadmin back to the organization picker
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


@patch("app.email.services._mailgun_send")
def test_superadmin_test_email_goes_to_the_typed_address(mock_send, client, app):
    mock_send.return_value = (True, "<test@x>", None)
    with app.app_context():
        sa = create_user("sa-test-to@test.com", "securepassword1", role="superadmin")
        sa.totp_enabled = True
        db.session.commit()

    client.post("/auth/login", data={"email": "sa-test-to@test.com", "password": "securepassword1"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    response = client.post("/admin/email/test", data={"to": "Oma.Posti@Example.fi"}, follow_redirects=True)
    assert mock_send.call_args.kwargs["to_email"] == "oma.posti@example.fi"
    assert "Testiviesti lähetetty osoitteeseen oma.posti@example.fi" in response.get_data(as_text=True)

    mock_send.reset_mock()
    response = client.post("/admin/email/test", data={"to": "ei-osoite"}, follow_redirects=True)
    assert not mock_send.called
    assert "Anna kelvollinen sähköpostiosoite" in response.get_data(as_text=True)
