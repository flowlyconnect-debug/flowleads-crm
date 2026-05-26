from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.automations.models import Automation, AutomationLog
from app.automations.services import (
    AutomationEngine,
    AutomationService,
    evaluate_trigger_conditions,
)
from app.extensions import db
from app.leads.models import Lead, PipelineStage
from app.leads.services import LeadService, get_default_stage, seed_default_pipeline_stages
from app.tasks.models import Task
from app.users.services import create_organization, create_user


def _setup_org(app, slug="auto-org"):
    with app.app_context():
        org = create_organization(f"Org {slug}", slug)
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
        other_org = create_organization(f"Other {slug}", f"{slug}-other")
        db.session.flush()
        other_user = create_user(
            f"user-{slug}-other@test.com",
            "securepassword1",
            role="user",
            organization_id=other_org.id,
        )
        db.session.commit()
        stage = get_default_stage(org.id)
        other_stage = get_default_stage(other_org.id)
        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "user_id": user.id,
            "stage_id": stage.id,
            "other_org_id": other_org.id,
            "other_user_id": other_user.id,
            "other_stage_id": other_stage.id,
        }


def _create_automation(org_id, admin_id, **kwargs):
    data = {
        "name": kwargs.get("name", "Test automation"),
        "trigger_type": kwargs.get("trigger_type", "lead_created"),
        "trigger_config": kwargs.get("trigger_config", {}),
        "is_active": kwargs.get("is_active", True),
        "actions": kwargs.get(
            "actions",
            [
                {
                    "action_type": "create_task",
                    "action_config": {
                        "title": "Auto task",
                        "due_days": 1,
                        "assign_to": "owner",
                    },
                }
            ],
        ),
    }
    return AutomationService.create(data, org_id, admin_id)


def test_lead_created_trigger_fires_task(app):
    ctx = _setup_org(app, "created-trigger")
    with app.app_context():
        _create_automation(ctx["org_id"], ctx["admin_id"])
        db.session.commit()

        lead = LeadService.create(
            {"email": "new@example.com", "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.commit()

        tasks = Task.query.filter_by(lead_id=lead.id, title="Auto task").all()
        assert len(tasks) == 1
        logs = AutomationLog.query.filter_by(lead_id=lead.id, result="success").all()
        assert len(logs) >= 1


def test_stage_changed_condition(app):
    ctx = _setup_org(app, "stage-cond")
    with app.app_context():
        stages = PipelineStage.query.filter_by(organization_id=ctx["org_id"]).all()
        target = next(s for s in stages if s.name == "Contacted")
        _create_automation(
            ctx["org_id"],
            ctx["admin_id"],
            trigger_type="lead_stage_changed",
            trigger_config={"to_stage_id": target.id},
            name="Stage only",
        )
        db.session.commit()

        lead = LeadService.create(
            {"email": "stage@example.com", "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.flush()
        Task.query.filter_by(lead_id=lead.id).delete()
        db.session.flush()

        LeadService.move_stage(lead.id, target.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()

        assert Task.query.filter_by(lead_id=lead.id, title="Auto task").count() == 1

        other_stage = next(s for s in stages if s.id != target.id and s.name != target.name)
        lead2 = LeadService.create(
            {"email": "stage2@example.com", "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.flush()
        LeadService.move_stage(lead2.id, other_stage.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        assert Task.query.filter_by(lead_id=lead2.id, title="Auto task").count() == 0


def test_score_changed_crosses_threshold(app):
    ctx = _setup_org(app, "score-cond")
    with app.app_context():
        _create_automation(
            ctx["org_id"],
            ctx["admin_id"],
            trigger_type="lead_score_changed",
            trigger_config={"threshold": 80, "operator": "crosses_above"},
            name="High score",
            actions=[
                {
                    "action_type": "add_tag",
                    "action_config": {"tag": "hot"},
                }
            ],
        )
        db.session.commit()

        lead = LeadService.create(
            {"email": "score@example.com", "score": 70, "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.commit()
        assert "hot" not in (lead.tags or [])

        LeadService.update(
            lead.id,
            {"score": 85},
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.commit()
        lead = db.session.get(Lead, lead.id)
        assert "hot" in (lead.tags or [])


def test_change_stage_action(app):
    ctx = _setup_org(app, "change-stage")
    with app.app_context():
        stages = PipelineStage.query.filter_by(organization_id=ctx["org_id"]).all()
        won = next(s for s in stages if s.name == "Won")
        _create_automation(
            ctx["org_id"],
            ctx["admin_id"],
            actions=[
                {
                    "action_type": "change_stage",
                    "action_config": {"stage_id": won.id},
                }
            ],
        )
        db.session.commit()

        lead = LeadService.create(
            {"email": "won@example.com", "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.commit()
        lead = db.session.get(Lead, lead.id)
        assert lead.stage_id == won.id


def test_disabled_automation_does_not_fire(app):
    ctx = _setup_org(app, "disabled")
    with app.app_context():
        _create_automation(ctx["org_id"], ctx["admin_id"], is_active=False)
        db.session.commit()

        lead = LeadService.create(
            {"email": "inactive@example.com", "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.commit()
        assert Task.query.filter_by(lead_id=lead.id, title="Auto task").count() == 0


def test_cross_tenant_automation_isolated(app):
    ctx = _setup_org(app, "cross-tenant")
    with app.app_context():
        _create_automation(ctx["org_id"], ctx["admin_id"])
        db.session.commit()

        lead = LeadService.create(
            {"email": "other@example.com", "assigned_to": ctx["other_user_id"]},
            ctx["other_org_id"],
            ctx["other_user_id"],
            actor_role="admin",
        )
        db.session.commit()
        assert Task.query.filter_by(lead_id=lead.id, title="Auto task").count() == 0
        logs = AutomationLog.query.filter_by(organization_id=ctx["org_id"], lead_id=lead.id).all()
        assert len(logs) == 0


def test_failed_action_logged_without_breaking_lead_create(app):
    ctx = _setup_org(app, "fail-safe")
    with app.app_context():
        _create_automation(
            ctx["org_id"],
            ctx["admin_id"],
            actions=[
                {
                    "action_type": "send_webhook",
                    "action_config": {
                        "url": "http://127.0.0.1:1/invalid",
                        "method": "POST",
                        "body_template": "{}",
                    },
                }
            ],
        )
        db.session.commit()

        lead = LeadService.create(
            {"email": "safe@example.com", "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.commit()
        assert lead.id is not None
        logs = AutomationLog.query.filter_by(lead_id=lead.id).all()
        assert any(log.result == "failed" for log in logs)


def test_evaluate_trigger_conditions_min_score_and_source():
    lead = Lead(source="n8n", score=70)
    assert evaluate_trigger_conditions(
        "lead_created",
        {"min_score": 60, "source": ["n8n"]},
        {},
        lead,
    )
    assert not evaluate_trigger_conditions(
        "lead_created",
        {"min_score": 80, "source": ["n8n"]},
        {},
        lead,
    )


def test_notify_user_action(app):
    ctx = _setup_org(app, "notify")
    with app.app_context():
        from app.notifications.models import Notification

        _create_automation(
            ctx["org_id"],
            ctx["admin_id"],
            actions=[
                {
                    "action_type": "notify_user",
                    "action_config": {
                        "user_id": ctx["admin_id"],
                        "title": "Test",
                        "message": "Hello",
                    },
                }
            ],
        )
        db.session.commit()

        lead = LeadService.create(
            {"email": "notify@example.com", "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.commit()
        assert (
            Notification.query.filter_by(user_id=ctx["admin_id"], title="Test").count() == 1
        )


@patch("urllib.request.urlopen")
def test_webhook_action_success(mock_urlopen, app):
    ctx = _setup_org(app, "webhook")
    mock_resp = mock_urlopen.return_value.__enter__.return_value
    mock_resp.status = 200

    with app.app_context():
        from app.automations.encryption import decrypt_webhook_headers, encrypt_webhook_headers
        from app.automations.models import AutomationAction

        automation = _create_automation(
            ctx["org_id"],
            ctx["admin_id"],
            actions=[],
        )
        enc = encrypt_webhook_headers({"X-Secret": "s3cret"})
        action = AutomationAction(
            automation_id=automation.id,
            order_index=0,
            action_type="send_webhook",
            action_config={
                "url": "https://example.com/hook",
                "method": "POST",
                "body_template": '{"id":"{{lead.id}}"}',
                "encrypted_headers": enc,
            },
        )
        db.session.add(action)
        db.session.commit()

        lead = LeadService.create(
            {"email": "hook@example.com", "company": "Acme"},
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.flush()
        ok, err = AutomationEngine.execute_action(
            action, lead, {}, organization_id=ctx["org_id"]
        )
        assert ok is True
        assert err is None
        decrypted = decrypt_webhook_headers(enc)
        assert decrypted["X-Secret"] == "s3cret"


def test_seed_default_automations(app):
    ctx = _setup_org(app, "seed")
    with app.app_context():
        from app.automations.seed import seed_default_automations

        seed_default_automations(ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        names = {a.name for a in Automation.query.filter_by(organization_id=ctx["org_id"]).all()}
        assert "Passiivinen liidi" in names
        assert "Korkean potentiaalin liidi" in names
        assert "Tarjous lähetetty" in names
