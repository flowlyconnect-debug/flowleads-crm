"""Mailgun inbound route -> handle_inbound_reply -> SequenceService.handle_reply."""
import hashlib
import hmac

from app.email.models import EmailLog
from app.email.services import handle_inbound_reply
from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService, get_default_stage
from app.sequences.models import EmailSequence, EmailSequenceEnrollment
from app.users.services import create_organization, create_user


def _setup(app, slug="inbound-org"):
    with app.app_context():
        org = create_organization("Inbound Org", slug)
        db.session.flush()
        admin = create_user(f"admin-{slug}@test.com", "securepassword1", role="admin", organization_id=org.id)
        db.session.commit()
        stage = get_default_stage(org.id)
        lead = LeadService.create({"email": "lead@test.com", "first_name": "Lea"}, org.id, admin.id)
        db.session.commit()

        sequence = EmailSequence(
            organization_id=org.id,
            name="Follow-up",
            trigger_type="manual",
            trigger_config={"stop_on_reply": True},
        )
        db.session.add(sequence)
        db.session.flush()

        enrollment = EmailSequenceEnrollment(
            sequence_id=sequence.id,
            lead_id=lead.id,
            organization_id=org.id,
            enrolled_by=admin.id,
            status="active",
        )
        db.session.add(enrollment)

        log = EmailLog(
            lead_id=lead.id,
            user_id=admin.id,
            organization_id=org.id,
            subject="Hello",
            body_html="<p>hi</p>",
            status="sent",
            mailgun_message_id="<original-message@mg.example.com>",
        )
        db.session.add(log)
        db.session.commit()
        return {
            "org_id": org.id,
            "lead_id": lead.id,
            "enrollment_id": enrollment.id,
            "log_id": log.id,
        }


def test_inbound_reply_matches_by_in_reply_to_and_unenrolls(app):
    ctx = _setup(app, "reply-match")
    with app.app_context():
        matched = handle_inbound_reply(
            {
                "In-Reply-To": "<original-message@mg.example.com>",
                "sender": "lead@test.com",
                "body-plain": "Thanks, not interested anymore.",
            }
        )
        db.session.commit()
        assert matched is True

        enrollment = db.session.get(EmailSequenceEnrollment, ctx["enrollment_id"])
        assert enrollment.status == "cancelled"

        assert (
            Activity.query.filter_by(lead_id=ctx["lead_id"], type="email_reply_received").count()
            == 1
        )


def test_inbound_reply_matches_via_references_header(app):
    ctx = _setup(app, "reply-refs")
    with app.app_context():
        matched = handle_inbound_reply(
            {
                "References": "<something-else@mg> <original-message@mg.example.com>",
                "sender": "lead@test.com",
                "body-plain": "Reply body",
            }
        )
        db.session.commit()
        assert matched is True


def test_inbound_reply_no_match_returns_false(app):
    _setup(app, "reply-no-match")
    with app.app_context():
        matched = handle_inbound_reply(
            {"In-Reply-To": "<unknown@mg.example.com>", "sender": "x@test.com", "body-plain": "hi"}
        )
        assert matched is False


def test_inbound_webhook_route_requires_valid_signature(client, app):
    client.application.config["MAILGUN_WEBHOOK_SIGNING_KEY"] = "key"
    response = client.post(
        "/api/webhooks/mailgun/inbound",
        data={"timestamp": "1", "token": "t", "signature": "bad", "In-Reply-To": "<x@mg>"},
    )
    assert response.status_code == 403


def test_inbound_webhook_route_processes_valid_reply(client, app):
    ctx = _setup(app, "reply-route")
    signing_key = "test-signing-key"
    client.application.config["MAILGUN_WEBHOOK_SIGNING_KEY"] = signing_key
    timestamp = "1529006854"
    token = "abc123"
    signature = hmac.new(
        signing_key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256
    ).hexdigest()

    response = client.post(
        "/api/webhooks/mailgun/inbound",
        data={
            "timestamp": timestamp,
            "token": token,
            "signature": signature,
            "In-Reply-To": "<original-message@mg.example.com>",
            "sender": "lead@test.com",
            "body-plain": "Stop emailing me",
        },
    )
    assert response.status_code == 200
    with app.app_context():
        enrollment = db.session.get(EmailSequenceEnrollment, ctx["enrollment_id"])
        assert enrollment.status == "cancelled"
