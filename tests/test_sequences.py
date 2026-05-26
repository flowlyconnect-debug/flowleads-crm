import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService, get_default_stage
from app.sequences.models import (
    EmailSequence,
    EmailSequenceEnrollment,
    EmailSequenceSent,
    EmailSequenceStep,
)
from app.sequences.services import (
    SequenceService,
    SequenceServiceError,
    _calculate_next_send_at,
    append_unsubscribe_footer,
)
from app.sequences.tokens import generate_unsubscribe_token, verify_unsubscribe_token
from app.api.services import create_api_key
from app.users.services import create_organization, create_user


def _setup_org(app, slug="seq-org"):
    with app.app_context():
        org = create_organization(f"Seq Org {slug}", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        other = create_organization(f"Other {slug}", f"{slug}-other")
        db.session.flush()
        create_user(
            f"admin-other-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=other.id,
        )
        db.session.commit()
        stage = get_default_stage(org.id)
        other_stage = get_default_stage(other.id)
        return {
            "org_id": org.id,
            "other_org_id": other.id,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "stage_id": stage.id,
            "other_stage_id": other_stage.id,
        }


def _auth_headers(key: str):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _create_api_key(app, org_id):
    with app.app_context():
        _, full_key = create_api_key(org_id, "seq test", test_mode=True)
        db.session.commit()
        return full_key


def _create_sequence(app, org_id, user_id, **kwargs):
    with app.app_context():
        seq = SequenceService.create_sequence(
            {
                "name": kwargs.get("name", "Welcome"),
                "is_active": kwargs.get("is_active", True),
                "trigger_type": kwargs.get("trigger_type", "manual"),
                "trigger_config": kwargs.get("trigger_config"),
            },
            org_id,
            user_id,
        )
        step = SequenceService.add_step(
            seq.id,
            {
                "order_index": 0,
                "delay_days": kwargs.get("delay_days", 0),
                "delay_hours": kwargs.get("delay_hours", 0),
                "subject_template": kwargs.get("subject", "Hello {{first_name}}"),
                "body_html_template": kwargs.get("body_html", "<p>Hi {{name}}</p>"),
                "body_text_template": kwargs.get("body_text", "Hi {{name}}"),
            },
            org_id,
        )
        db.session.commit()
        return seq.id, step.id


def _create_lead(app, org_id, email="lead@seq.com", stage_id=None, unsubscribed=False):
    with app.app_context():
        lead = LeadService.create(
            {"email": email, "first_name": "Ada", "company": "Acme", "stage_id": stage_id},
            org_id,
            None,
            actor_role="admin",
        )
        if unsubscribed:
            lead.unsubscribed = True
        db.session.commit()
        return lead.id


# --- Scheduling ---


def test_step_delay_calculation(app):
    ctx = _setup_org(app, "delay")
    with app.app_context():
        seq_id, step_id = _create_sequence(app, ctx["org_id"], ctx["admin_id"], delay_days=2, delay_hours=3)
        step = db.session.get(EmailSequenceStep, step_id)
        base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        next_at = _calculate_next_send_at(step, base)
        assert next_at == datetime(2026, 1, 3, 15, 0, tzinfo=timezone.utc)


def test_enrollment_rejects_unsubscribed_lead(app):
    ctx = _setup_org(app, "unsub-enroll")
    seq_id, _ = _create_sequence(app, ctx["org_id"], ctx["admin_id"])
    lead_id = _create_lead(app, ctx["org_id"], unsubscribed=True)
    with app.app_context():
        with pytest.raises(SequenceServiceError) as exc:
            SequenceService.enroll_lead(lead_id, seq_id, organization_id=ctx["org_id"])
        assert exc.value.code == "unsubscribed"


def test_duplicate_active_enrollment_prevented(app):
    ctx = _setup_org(app, "dup")
    seq_id, _ = _create_sequence(app, ctx["org_id"], ctx["admin_id"])
    lead_id = _create_lead(app, ctx["org_id"])
    with app.app_context():
        SequenceService.enroll_lead(lead_id, seq_id, organization_id=ctx["org_id"])
        db.session.commit()
        with pytest.raises(SequenceServiceError) as exc:
            SequenceService.enroll_lead(lead_id, seq_id, organization_id=ctx["org_id"])
        assert exc.value.code == "duplicate_enrollment"


@patch("app.email.services._mailgun_send", return_value=(True, "msg-1", None))
def test_process_due_steps_sends_and_schedules_next(mock_send, app):
    ctx = _setup_org(app, "due")
    with app.app_context():
        seq = SequenceService.create_sequence(
            {"name": "Two-step", "is_active": True},
            ctx["org_id"],
            ctx["admin_id"],
        )
        step0 = SequenceService.add_step(
            seq.id,
            {
                "order_index": 0,
                "delay_days": 0,
                "delay_hours": 0,
                "subject_template": "Step 0",
                "body_html_template": "<p>0</p>",
            },
            ctx["org_id"],
        )
        SequenceService.add_step(
            seq.id,
            {
                "order_index": 1,
                "delay_days": 1,
                "delay_hours": 0,
                "subject_template": "Step 1",
                "body_html_template": "<p>1</p>",
            },
            ctx["org_id"],
        )
        lead = LeadService.create(
            {"email": "due@seq.com", "first_name": "A"},
            ctx["org_id"],
            None,
            actor_role="admin",
        )
        enrollment = SequenceService.enroll_lead(lead.id, seq.id, organization_id=ctx["org_id"])
        enrollment.next_send_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()

        count = SequenceService.process_due_steps()
        assert count == 1

        sent = EmailSequenceSent.query.filter_by(enrollment_id=enrollment.id).all()
        assert len(sent) == 1
        enrollment = db.session.get(EmailSequenceEnrollment, enrollment.id)
        assert enrollment.current_step_index == 1
        assert enrollment.status == "active"
        assert enrollment.next_send_at is not None
        assert mock_send.called
        call_kwargs = mock_send.call_args.kwargs
        assert "Unsubscribe" in call_kwargs["body_html"]


@patch("app.email.services._mailgun_send", return_value=(True, "msg-1", None))
def test_sequence_completes_after_last_step(mock_send, app):
    ctx = _setup_org(app, "complete")
    seq_id, _ = _create_sequence(app, ctx["org_id"], ctx["admin_id"])
    lead_id = _create_lead(app, ctx["org_id"], email="done@seq.com")
    with app.app_context():
        enrollment = SequenceService.enroll_lead(lead_id, seq_id, organization_id=ctx["org_id"])
        enrollment.next_send_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()
        SequenceService.process_due_steps()
        enrollment = db.session.get(EmailSequenceEnrollment, enrollment.id)
        assert enrollment.status == "completed"
        assert enrollment.completed_at is not None
        assert enrollment.next_send_at is None


def test_unsubscribe_sets_flag_and_stops_emails(app, client):
    ctx = _setup_org(app, "unsub-link")
    seq_id, _ = _create_sequence(app, ctx["org_id"], ctx["admin_id"])
    lead_id = _create_lead(app, ctx["org_id"])
    with app.app_context():
        token = generate_unsubscribe_token(lead_id, seq_id)
        SequenceService.enroll_lead(lead_id, seq_id, organization_id=ctx["org_id"])
        db.session.commit()

    response = client.get(f"/unsubscribe?token={token}")
    assert response.status_code == 200
    assert b"unsubscribed" in response.data.lower()

    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert lead.unsubscribed is True
        active = EmailSequenceEnrollment.query.filter_by(
            lead_id=lead_id, sequence_id=seq_id, status="active"
        ).first()
        assert active is None


def test_unsubscribe_token_roundtrip(app):
    with app.app_context():
        token = generate_unsubscribe_token(1, 2)
        data = verify_unsubscribe_token(token)
        assert data == {"lead_id": 1, "sequence_id": 2}


def test_auto_enroll_on_lead_created(app):
    ctx = _setup_org(app, "auto-create")
    with app.app_context():
        seq = SequenceService.create_sequence(
            {
                "name": "Auto welcome",
                "is_active": True,
                "trigger_type": "on_lead_created",
                "trigger_config": {},
            },
            ctx["org_id"],
            ctx["admin_id"],
        )
        SequenceService.add_step(
            seq.id,
            {
                "order_index": 0,
                "subject_template": "Welcome",
                "body_html_template": "<p>Hi</p>",
            },
            ctx["org_id"],
        )
        seq_id = seq.id
        db.session.commit()

    lead_id = _create_lead(app, ctx["org_id"], email="auto@seq.com")
    with app.app_context():
        enrollment = EmailSequenceEnrollment.query.filter_by(
            lead_id=lead_id, sequence_id=seq_id
        ).first()
        assert enrollment is not None
        assert enrollment.status == "active"


def test_auto_enroll_on_stage_change_matching_stage(app):
    ctx = _setup_org(app, "auto-stage")
    from app.leads.models import PipelineStage

    with app.app_context():
        seq = SequenceService.create_sequence(
            {
                "name": "Stage seq",
                "is_active": True,
                "trigger_type": "on_stage_change",
                "trigger_config": {"stage_id": ctx["stage_id"]},
            },
            ctx["org_id"],
            ctx["admin_id"],
        )
        SequenceService.add_step(
            seq.id,
            {
                "order_index": 0,
                "subject_template": "Moved",
                "body_html_template": "<p>Stage</p>",
            },
            ctx["org_id"],
        )
        lead = LeadService.create(
            {"email": "stage@seq.com"},
            ctx["org_id"],
            None,
            actor_role="admin",
        )
        db.session.flush()
        seq_id = seq.id
        lead_id = lead.id
        stages = PipelineStage.query.filter_by(organization_id=ctx["org_id"]).all()
        other_stage = next(s for s in stages if s.id != lead.stage_id)
        LeadService.move_stage(lead_id, other_stage.id, ctx["org_id"], None)
        db.session.commit()
        assert (
            EmailSequenceEnrollment.query.filter_by(lead_id=lead_id, sequence_id=seq_id).first()
            is None
        )

        LeadService.move_stage(lead_id, ctx["stage_id"], ctx["org_id"], None)
        db.session.commit()
        enrollment = EmailSequenceEnrollment.query.filter_by(
            lead_id=lead_id, sequence_id=seq_id
        ).first()
        assert enrollment is not None


@patch("app.email.services._mailgun_send", return_value=(True, "msg-1", None))
def test_n8n_api_enroll_endpoint(mock_send, app, client):
    ctx = _setup_org(app, "api-enroll")
    seq_id, _ = _create_sequence(app, ctx["org_id"], ctx["admin_id"])
    lead_id = _create_lead(app, ctx["org_id"], email="api@seq.com")
    key = _create_api_key(app, ctx["org_id"])

    response = client.post(
        f"/api/v1/leads/{lead_id}/sequences/enroll",
        data=json.dumps({"sequence_id": seq_id}),
        headers=_auth_headers(key),
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["enrollment"]["sequence_id"] == seq_id


def test_cross_tenant_isolation(app, client):
    ctx = _setup_org(app, "tenant")
    seq_id, _ = _create_sequence(app, ctx["org_id"], ctx["admin_id"])
    other_key = _create_api_key(app, ctx["other_org_id"])
    lead_id = _create_lead(app, ctx["org_id"])

    response = client.post(
        f"/api/v1/leads/{lead_id}/sequences/enroll",
        data=json.dumps({"sequence_id": seq_id}),
        headers=_auth_headers(other_key),
    )
    assert response.status_code == 404


@patch("app.email.services._mailgun_send", return_value=(True, "msg-ok", None))
def test_one_failed_enrollment_does_not_block_others(mock_send, app):
    ctx = _setup_org(app, "isolate-fail")
    seq_id, _ = _create_sequence(app, ctx["org_id"], ctx["admin_id"])
    good_lead = _create_lead(app, ctx["org_id"], email="good@seq.com")
    bad_lead = _create_lead(app, ctx["org_id"], email="bad@seq.com")

    with app.app_context():
        bad = db.session.get(Lead, bad_lead)
        bad.email = None
        e1 = SequenceService.enroll_lead(good_lead, seq_id, organization_id=ctx["org_id"])
        e2 = SequenceService.enroll_lead(bad_lead, seq_id, organization_id=ctx["org_id"])
        now = datetime.now(timezone.utc) - timedelta(minutes=1)
        e1.next_send_at = now
        e2.next_send_at = now
        db.session.commit()

        count = SequenceService.process_due_steps()
        assert count == 1
        good_sent = EmailSequenceSent.query.filter_by(enrollment_id=e1.id).count()
        bad_sent = EmailSequenceSent.query.filter_by(enrollment_id=e2.id).count()
        assert good_sent == 1
        assert bad_sent == 0


def test_append_unsubscribe_footer(app):
    with app.app_context():
        html, text = append_unsubscribe_footer(
            "<p>Body</p>",
            "Body",
            lead_id=5,
            sequence_id=9,
        )
        assert "Unsubscribe" in html
        assert "unsubscribe" in text.lower()


def test_sequence_enrolled_activity_logged(app):
    ctx = _setup_org(app, "activity")
    seq_id, _ = _create_sequence(app, ctx["org_id"], ctx["admin_id"])
    lead_id = _create_lead(app, ctx["org_id"])
    with app.app_context():
        SequenceService.enroll_lead(lead_id, seq_id, organization_id=ctx["org_id"])
        db.session.commit()
        activity = Activity.query.filter_by(lead_id=lead_id, type="sequence_enrolled").first()
        assert activity is not None
