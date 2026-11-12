"""Production readiness: the open bugs of docs/TUOTANTOON_VIENTI_TILANNE.md."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.tasks.models import Task
from app.users.models import User
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
HELSINKI = ZoneInfo("Europe/Helsinki")


def _org(slug, role="admin"):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"{role}-{slug}@test.com", PASSWORD, role=role, organization_id=org.id)
    user.totp_enabled = True
    db.session.commit()
    return org, user


def _lead(org, ref="p1", **fields):
    from app.api.services import upsert_lead

    lead, _ = upsert_lead(org.id, {"company": f"As Oy {ref}", "source": "import", "source_ref": f"import-{ref}"}, match_by_email=False)
    for key, value in fields.items():
        setattr(lead, key, value)
    db.session.commit()
    return lead


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --- task times are Finnish time ---------------------------------------------------------------


def test_typed_task_time_is_finnish_time(app):
    from app.tasks.services import TaskService, local_to_utc

    assert local_to_utc(datetime(2026, 7, 1, 9, 0)) == datetime(2026, 7, 1, 6, 0, tzinfo=timezone.utc)  # EEST
    assert local_to_utc(datetime(2026, 1, 1, 9, 0)) == datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc)  # EET
    with app.app_context():
        org, user = _org("tz")
        task = TaskService.create({"title": "Soita", "due_date": "2026-12-01T09:30"}, org.id, user.id)
        assert _aware(task.due_date) == datetime(2026, 12, 1, 7, 30, tzinfo=timezone.utc)
        # a time with an offset (API) is only converted
        api = TaskService.create({"title": "API", "due_date": "2026-12-01T09:30:00Z"}, org.id, user.id)
        assert _aware(api.due_date).hour == 9
        # code passes aware UTC datetimes unchanged
        auto = TaskService.create({"title": "Auto", "due_date": datetime(2026, 12, 1, 5, tzinfo=timezone.utc)}, org.id, user.id)
        assert _aware(auto.due_date).hour == 5


def test_task_pages_show_finnish_time(client, app):
    from app.tasks.services import TaskService

    with app.app_context():
        org, user = _org("tzpage")
        task = TaskService.create({"title": "Aamusoitto", "due_date": "2030-03-04T08:15"}, org.id, user.id)
        db.session.commit()
        task_id, email = task.id, user.email
    _login(client, email)
    edit = client.get(f"/tasks/{task_id}/edit").get_data(as_text=True)
    assert 'value="2030-03-04T08:15"' in edit


def test_task_time_migration_converts_typed_tasks_only():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "m", Path(__file__).resolve().parents[1] / "migrations/versions/f6f7a8b9c0d1_task_times_to_utc.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stored = datetime(2026, 11, 20, 9, 0)  # typed 09.00, stored as if UTC
    fixed = module._shift(stored, to_utc=True)
    assert fixed == datetime(2026, 11, 20, 7, 0, tzinfo=timezone.utc)
    assert module._shift(fixed, to_utc=False).replace(tzinfo=None) == stored


def test_dashboard_task_counts_are_the_users_own(app):
    from app.analytics.dashboard_today import DashboardTodayService
    from app.tasks.services import TaskService

    with app.app_context():
        org, admin = _org("dashcount")
        other = create_user("other-dashcount@test.com", PASSWORD, role="user", organization_id=org.id)
        db.session.commit()
        past = datetime.now(timezone.utc) - timedelta(days=2)
        TaskService.create({"title": "Oma", "due_date": past}, org.id, admin.id)
        TaskService.create({"title": "Toisen", "due_date": past, "assigned_to": other.id}, org.id, admin.id)
        db.session.commit()
        assert DashboardTodayService.get_dashboard_metrics(org.id, admin.id)["overdue_tasks"] == 1
        assert DashboardTodayService.get_dashboard_metrics(org.id)["overdue_tasks"] == 2
        assert len(DashboardTodayService.get_today_data(org.id, admin.id)["overdue_tasks"]) == 1


# --- reports use the stage change ---------------------------------------------------------------


def test_won_reports_use_stage_change_not_last_edit(app):
    from app.analytics.services import AnalyticsService

    with app.app_context():
        org, _ = _org("winloss")
        now = datetime.now(timezone.utc)
        long_ago = now - timedelta(days=200)
        lead = _lead(org, status="won", stage_changed_at=long_ago, deal_value=1000)
        lead.updated_at = now  # a note today must not move the win to this month
        db.session.commit()
        chart = AnalyticsService.get_won_deals_monthly_chart(org.id, months=3)
        assert sum(chart["won_counts"]) == 0
        report = AnalyticsService.get_pipeline_report(org.id, now - timedelta(days=30), now)
        assert report["won_lost"]["won"] == 0 if "won_lost" in report else report["won"] == 0
        # and it is counted in the month it was really won
        then = AnalyticsService.get_pipeline_report(org.id, long_ago - timedelta(days=1), long_ago + timedelta(days=1))
        assert (then["won_lost"]["won"] if "won_lost" in then else then["won"]) == 1


# --- email events ------------------------------------------------------------------------------


def test_opens_and_clicks_reach_sequence_statistics(app):
    from app.email.models import EmailLog
    from app.email.services import handle_mailgun_webhook
    from app.sequences.models import EmailSequence, EmailSequenceEnrollment, EmailSequenceSent, EmailSequenceStep

    with app.app_context():
        org, user = _org("seqstats")
        lead = _lead(org, email="pj@example.fi")
        sequence = EmailSequence(organization_id=org.id, name="S", is_active=True, created_by=user.id)
        db.session.add(sequence)
        db.session.flush()
        step = EmailSequenceStep(sequence_id=sequence.id, order_index=0, delay_days=0, subject_template="Hei", body_html_template="x")
        db.session.add(step)
        enrollment = EmailSequenceEnrollment(sequence_id=sequence.id, lead_id=lead.id, organization_id=org.id, status="active")
        db.session.add(enrollment)
        log = EmailLog(organization_id=org.id, lead_id=lead.id, subject="Hei", status="sent", mailgun_message_id="<abc@mg>")
        db.session.add(log)
        db.session.flush()
        sent = EmailSequenceSent(enrollment_id=enrollment.id, step_id=step.id, lead_id=lead.id, email_log_id=log.id)
        db.session.add(sent)
        db.session.commit()

        assert handle_mailgun_webhook({"event": "clicked", "message": {"headers": {"message-id": "abc@mg"}}})
        db.session.commit()
        db.session.refresh(sent)
        assert sent.opened_at is not None and sent.clicked_at is not None
        # a late "delivered" does not undo the click
        handle_mailgun_webhook({"event": "delivered", "message": {"headers": {"message-id": "abc@mg"}}})
        db.session.commit()
        assert db.session.get(EmailLog, log.id).status == "clicked"


# --- automations ---------------------------------------------------------------------------------


def test_no_activity_automation_fires_once_per_quiet_period(app):
    from app.automations.models import Automation, AutomationAction, AutomationLog
    from app.automations.services import AutomationEngine

    with app.app_context():
        org, user = _org("quiet")
        lead = _lead(org)
        lead.created_at = datetime.now(timezone.utc) - timedelta(days=30)
        automation = Automation(organization_id=org.id, name="Hiljainen", trigger_type="lead_no_activity", trigger_config={"days": 14}, is_active=True)
        db.session.add(automation)
        db.session.flush()
        db.session.add(AutomationAction(automation_id=automation.id, order_index=0, action_type="add_tag", action_config={"tag": "hiljainen"}))
        from app.leads.models import Activity

        Activity.query.filter_by(lead_id=lead.id).delete()
        db.session.commit()
        AutomationEngine.run_no_activity_checks()
        db.session.commit()
        AutomationEngine.run_no_activity_checks()  # the next day
        db.session.commit()
        assert AutomationLog.query.filter_by(automation_id=automation.id).count() == 1


def test_automation_edit_keeps_secret_webhook_headers(app):
    from app.automations.models import AutomationAction
    from app.automations.services import AutomationService

    with app.app_context():
        org, user = _org("hdr")
        automation = AutomationService.create(
            {
                "name": "Webhook",
                "trigger_type": "lead_created",
                "actions": [
                    {
                        "action_type": "send_webhook",
                        "action_config": {"url": "https://example.com/hook", "secret_headers": {"Authorization": "Bearer x"}},
                    }
                ],
            },
            org.id,
            user.id,
        )
        db.session.commit()
        stored = AutomationAction.query.filter_by(automation_id=automation.id).one().action_config["encrypted_headers"]
        # the edit form sends the action back without the secret
        AutomationService.update(
            automation.id,
            {"name": "Webhook 2", "actions": [{"action_type": "send_webhook", "action_config": {"url": "https://example.com/hook"}}]},
            org.id,
        )
        db.session.commit()
        action = AutomationAction.query.filter_by(automation_id=automation.id).one()
        assert action.action_config["encrypted_headers"] == stored


def test_switched_off_modules_do_not_run_in_the_background(app):
    from app.automations.models import Automation, AutomationLog
    from app.automations.services import AutomationEngine

    with app.app_context():
        org, _ = _org("featoff")
        org.enabled_features = []
        lead = _lead(org)
        db.session.add(Automation(organization_id=org.id, name="A", trigger_type="lead_created", trigger_config={}, is_active=True))
        db.session.commit()
        AutomationEngine.trigger("lead_created", {"lead_id": lead.id}, org.id)
        assert AutomationLog.query.count() == 0


# --- chat messages -----------------------------------------------------------------------------


def test_slack_message_names_the_event(app):
    from app.webhooks.services import WebhookService

    with app.app_context():
        org, _ = _org("slack")
        lead = _lead(org)
        payload = {"organization_id": org.id, "data": {"lead": {"id": lead.id}}}
        slack = WebhookService.format_payload("lead.stage_changed", payload, "slack")
        assert slack["text"].startswith("Liidin vaihe muuttui: As Oy p1")
        teams = WebhookService.format_payload("proposal.accepted", {"organization_id": org.id, "data": {"proposal": {"title": "Putket", "total": "1200.00", "lead_id": lead.id}}}, "teams")
        body = teams["attachments"][0]["content"]["body"]
        assert body[0]["text"] == "Tarjous hyväksytty: As Oy p1"
        assert {"title": "Tarjous", "value": "Putket"} in body[1]["facts"]
        created = WebhookService.format_payload("lead.created", {"data": {"lead": {"company": "As Oy Uusi", "name": "Pekka"}}}, "slack")
        assert created["text"] == "Uusi liidi: As Oy Uusi"


# --- secrets are not in the cookie -------------------------------------------------------------


def test_new_api_key_is_not_in_the_session_cookie(client, app):
    with app.app_context():
        org, admin = _org("cookie")
        email = admin.email
    _login(client, email)
    client.post("/settings/api-keys", data={"name": "Testi"})
    with client.session_transaction() as session:
        dump = repr(dict(session))
    assert "fl_live" not in dump and "full_key" not in dump
    with app.app_context():
        from app.core.one_time_secrets import OneTimeSecret

        row = OneTimeSecret.query.one()
        assert "fl_" not in row.payload_encrypted


def test_one_time_secret_is_shown_once_and_expires(app):
    from app.core import one_time_secrets as ots

    with app.test_request_context():
        org, user = _org("ots")
        token = ots.stash("x", {"v": 1}, user.id)
        assert ots.peek("x", token, user.id) == {"v": 1}
        assert ots.peek("x", token, user.id + 1) is None  # another user
        assert ots.take("x", token, user.id) == {"v": 1}
        assert ots.take("x", token, user.id) is None
        expired = ots.stash("x", {"v": 2}, user.id, ttl_seconds=-1)
        assert ots.peek("x", expired, user.id) is None


# --- headers ---------------------------------------------------------------------------------


def test_content_security_policy(client, app):
    page = client.get("/auth/login")
    policy = page.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in policy and "object-src 'none'" in policy and "frame-ancestors 'self'" in policy
    assert "Content-Security-Policy" not in client.get("/api/v1/health").headers


# --- purge dry run ----------------------------------------------------------------------------


def test_purge_dry_run_counts_each_row_once(app):
    from app.leads.purge import purge_all_leads
    from app.listings.models import Listing, ListingMatch

    with app.app_context():
        org, _ = _org("purgecount")
        lead = _lead(org)
        listing = Listing(source="import", external_id="x", maakunta="Uusimaa", taloyhtio="As Oy X")
        db.session.add(listing)
        db.session.flush()
        db.session.add(ListingMatch(organization_id=org.id, listing_id=listing.id, lead_id=lead.id, status="lead_created"))
        db.session.commit()
        dry = purge_all_leads(with_listings=True, dry_run=True)
        assert dry["listing_matches"] == 1
        real = purge_all_leads(with_listings=True, dry_run=False)
        assert real["listing_matches"] == 1


# --- proposal prefix, invited users ----------------------------------------------------------


def test_proposal_number_prefix(app):
    from app.proposals.services import ProposalService, ProposalServiceError
    from app.proposals.utils import generate_reference_number

    with app.app_context():
        org, _ = _org("prefix")
        assert generate_reference_number(org.id).startswith("FLW-")
        ProposalService.save_org_settings(org.id, {"proposal_number_prefix": "abc"})
        assert generate_reference_number(org.id).startswith("ABC-")
        with pytest.raises(ProposalServiceError):
            ProposalService.save_org_settings(org.id, {"proposal_number_prefix": "A B/C"})


def test_team_list_shows_invited_users(client, app):
    with app.app_context():
        org, admin = _org("invited")
        create_user("uusi-invited@test.com", PASSWORD, role="user", organization_id=org.id)
        db.session.commit()
        email = admin.email
    _login(client, email)
    page = client.get("/settings/team").get_data(as_text=True)
    assert "Kutsuttu" in page and "Kirjautunut" in page  # the admin has signed in
    with app.app_context():
        assert User.query.filter_by(email=email).one().last_login_at is not None


# --- system status ---------------------------------------------------------------------------


def test_system_status_page_and_cli(client, app, runner):
    with app.app_context():
        superadmin = create_user("super-system@test.com", PASSWORD, role="superadmin")
        superadmin.totp_enabled = True
        db.session.commit()
    app.config["MAILGUN_DOMAIN"] = "sandbox123.mailgun.org"
    try:
        _login(client, "super-system@test.com")
        page = client.get("/admin/system").get_data(as_text=True)
        assert "Järjestelmän tila" in page and "sandbox-domain" in page
        assert "test-key" not in page  # values are never shown
        result = runner.invoke(args=["system-check"])
        assert "Sähköposti" in result.output and "VIRHE" in result.output
        assert result.exit_code == 1
    finally:
        app.config["MAILGUN_DOMAIN"] = "example.com"
