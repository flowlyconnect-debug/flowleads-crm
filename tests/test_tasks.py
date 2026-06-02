from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService, get_default_stage
from app.tasks.models import OrganizationSettings, Task
from app.tasks.services import TaskService, TaskServiceError, get_task_for_org
from app.tasks.settings import get_organization_settings
from app.users.services import create_organization, create_user


def _setup_org_with_users(app, slug="tasks-org"):
    with app.app_context():
        org = create_organization("Tasks Org", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@acme.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        user = create_user(
            f"user-{slug}@acme.com",
            "securepassword1",
            role="user",
            organization_id=org.id,
        )
        other_org = create_organization("Other Org", f"{slug}-other")
        db.session.flush()
        other_admin = create_user(
            f"admin-{slug}-other@acme.com",
            "securepassword1",
            role="admin",
            organization_id=other_org.id,
        )
        db.session.commit()
        stage = get_default_stage(org.id)
        other_stage = get_default_stage(other_org.id)
        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "user_id": user.id,
            "admin_email": admin.email,
            "user_email": user.email,
            "stage_id": stage.id,
            "other_org_id": other_org.id,
            "other_admin_id": other_admin.id,
            "other_stage_id": other_stage.id,
        }


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _create_lead(app, org_id, stage_id, **kwargs):
    with app.app_context():
        lead = LeadService.create(
            {
                "email": kwargs.get("email", "lead@tasks.com"),
                "company": kwargs.get("company", "Acme"),
                "assigned_to": kwargs.get("assigned_to"),
                "stage_id": stage_id,
            },
            org_id,
            kwargs.get("user_id"),
            actor_role="admin",
        )
        db.session.commit()
        return lead.id


def test_create_standalone_task(app):
    ctx = _setup_org_with_users(app)
    due = datetime.now(timezone.utc) + timedelta(days=1)
    with app.app_context():
        task = TaskService.create(
            {"title": "Call client", "due_date": due, "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        assert task.id is not None
        assert task.lead_id is None
        assert task.status == "pending"


def test_create_task_linked_to_lead_logs_activity(app):
    ctx = _setup_org_with_users(app, "task-lead")
    with app.app_context():
        settings = get_organization_settings(ctx["org_id"])
        settings.auto_task_on_new_lead = False
        db.session.commit()
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], user_id=ctx["admin_id"])
    due = datetime.now(timezone.utc) + timedelta(days=2)
    with app.app_context():
        task = TaskService.create(
            {
                "title": "Follow up",
                "due_date": due,
                "assigned_to": ctx["user_id"],
                "type": "follow_up",
            },
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=lead_id,
        )
        db.session.commit()
        assert task.lead_id == lead_id
        assert (
            Activity.query.filter_by(lead_id=lead_id, type="task_created").count() == 1
        )


def test_complete_task_sets_status_and_activity(app):
    ctx = _setup_org_with_users(app, "task-complete")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    due = datetime.now(timezone.utc) + timedelta(days=1)
    with app.app_context():
        task = TaskService.create(
            {"title": "Done me", "due_date": due, "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=lead_id,
        )
        db.session.commit()
        TaskService.complete(task.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        refreshed = db.session.get(Task, task.id)
        assert refreshed.status == "completed"
        assert refreshed.completed_at is not None
        assert (
            Activity.query.filter_by(lead_id=lead_id, type="task_completed").count() == 1
        )


def test_get_overdue_tasks(app):
    ctx = _setup_org_with_users(app, "task-overdue")
    past = datetime.now(timezone.utc) - timedelta(days=2)
    with app.app_context():
        TaskService.create(
            {"title": "Late", "due_date": past, "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        overdue = TaskService.get_overdue(ctx["org_id"], user_id=ctx["user_id"])
        assert len(overdue) == 1
        assert overdue[0].is_overdue is True


def test_auto_task_on_new_lead(app):
    ctx = _setup_org_with_users(app, "auto-new")
    with app.app_context():
        settings = get_organization_settings(ctx["org_id"])
        settings.auto_task_on_new_lead = True
        db.session.commit()

    _create_lead(
        app,
        ctx["org_id"],
        ctx["stage_id"],
        email="newlead@acme.com",
        user_id=ctx["admin_id"],
        assigned_to=ctx["user_id"],
    )
    with app.app_context():
        tasks = Task.query.filter_by(organization_id=ctx["org_id"]).all()
        assert len(tasks) >= 1
        assert any(t.title == "Ota yhteyttä" for t in tasks)


def test_auto_task_disabled_on_new_lead(app):
    ctx = _setup_org_with_users(app, "auto-off")
    with app.app_context():
        settings = get_organization_settings(ctx["org_id"])
        settings.auto_task_on_new_lead = False
        db.session.commit()

    _create_lead(app, ctx["org_id"], ctx["stage_id"], email="noauto@acme.com")
    with app.app_context():
        assert Task.query.filter_by(organization_id=ctx["org_id"]).count() == 0


@patch("app.tasks.reminders._mailgun_send")
def test_send_reminders_marks_sent_and_logs_activity(mock_send, app):
    mock_send.return_value = (True, "msg-1", None)
    ctx = _setup_org_with_users(app, "reminder")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    past_reminder = datetime.now(timezone.utc) - timedelta(minutes=5)
    with app.app_context():
        task = TaskService.create(
            {
                "title": "Remind me",
                "due_date": datetime.now(timezone.utc) + timedelta(days=1),
                "assigned_to": ctx["user_id"],
                "reminder_at": past_reminder,
            },
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=lead_id,
        )
        db.session.commit()
        count = TaskService.send_reminders()
        assert count == 1
        refreshed = db.session.get(Task, task.id)
        assert refreshed.reminder_sent is True
        assert (
            Activity.query.filter_by(lead_id=lead_id, type="task_reminder_sent").count()
            == 1
        )


@patch("app.tasks.reminders._mailgun_send")
def test_reminder_not_sent_twice(mock_send, app):
    mock_send.return_value = (True, "msg-1", None)
    ctx = _setup_org_with_users(app, "reminder-once")
    past_reminder = datetime.now(timezone.utc) - timedelta(minutes=5)
    with app.app_context():
        task = TaskService.create(
            {
                "title": "Once",
                "due_date": datetime.now(timezone.utc) + timedelta(days=1),
                "assigned_to": ctx["user_id"],
                "reminder_at": past_reminder,
            },
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        assert TaskService.send_reminders() == 1
        assert TaskService.send_reminders() == 0


def test_cross_tenant_task_isolation(app):
    ctx = _setup_org_with_users(app, "tenant-task")
    due = datetime.now(timezone.utc) + timedelta(days=1)
    with app.app_context():
        task = TaskService.create(
            {"title": "Secret", "due_date": due, "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        task_id = task.id

        with pytest.raises(TaskServiceError) as exc:
            TaskService.complete(task_id, ctx["other_org_id"], ctx["other_admin_id"])
        assert exc.value.code == "not_found"

        with pytest.raises(TaskServiceError):
            get_task_for_org(task_id, ctx["other_org_id"])


def test_api_create_lead_task(app, client):
    ctx = _setup_org_with_users(app, "api-task")
    from app.api.services import create_api_key

    with app.app_context():
        _api_key, full_key = create_api_key(ctx["org_id"], "n8n")
        db.session.commit()

    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="api@lead.com")
    due = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    response = client.post(
        f"/api/v1/leads/{lead_id}/tasks",
        json={"title": "API task", "due_date": due, "assigned_to": ctx["user_id"]},
        headers={"Authorization": f"Bearer {full_key}"},
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["task"]["title"] == "API task"


def test_tasks_page_requires_login(client):
    response = client.get("/tasks")
    assert response.status_code == 302


def test_task_sections_overdue_visibility(app, client):
    ctx = _setup_org_with_users(app, "sections-overdue")
    _login(client, ctx["admin_email"])

    response = client.get("/tasks")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-section="overdue"' not in html

    with app.app_context():
        TaskService.create(
            {
                "title": "Late now",
                "due_date": datetime.now(timezone.utc) - timedelta(days=1),
                "assigned_to": ctx["admin_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()

    response = client.get("/tasks")
    html = response.get_data(as_text=True)
    assert 'data-section="overdue"' in html
    assert "Myöhässä" in html


def test_task_sections_today_and_week_split(app, client):
    ctx = _setup_org_with_users(app, "sections-today-week")
    _login(client, ctx["admin_email"])
    now = datetime.now(timezone.utc)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = start_today + timedelta(days=1, hours=10)
    end_of_week = start_today - timedelta(days=start_today.weekday()) + timedelta(days=6, hours=12)
    next_week = start_today - timedelta(days=start_today.weekday()) + timedelta(days=8, hours=9)
    with app.app_context():
        TaskService.create(
            {"title": "Today task", "due_date": start_today + timedelta(hours=9), "assigned_to": ctx["admin_id"]},
            ctx["org_id"],
            ctx["admin_id"],
        )
        TaskService.create(
            {"title": "Week task", "due_date": tomorrow, "assigned_to": ctx["admin_id"]},
            ctx["org_id"],
            ctx["admin_id"],
        )
        TaskService.create(
            {"title": "Later task", "due_date": next_week, "assigned_to": ctx["admin_id"]},
            ctx["org_id"],
            ctx["admin_id"],
        )
        if tomorrow > end_of_week:
            pytest.skip("Time boundary on Sunday; tomorrow is next week in UTC.")
        db.session.commit()

    response = client.get("/tasks")
    html = response.get_data(as_text=True)
    assert "Today task" in html
    assert "Week task" in html
    assert 'id="later-list"' in html
    assert "Later task" in html


def test_completed_tasks_hidden_by_default(app, client):
    ctx = _setup_org_with_users(app, "sections-completed")
    _login(client, ctx["admin_email"])
    due = datetime.now(timezone.utc) + timedelta(hours=1)
    with app.app_context():
        task = TaskService.create(
            {"title": "Complete me", "due_date": due, "assigned_to": ctx["admin_id"]},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        TaskService.complete(task.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()

    response = client.get("/tasks")
    html = response.get_data(as_text=True)
    assert "Näytä tehdyt tehtävät" in html
    assert 'id="completed-list" class="is-hidden"' in html


def test_checkbox_complete_updates_status_via_ajax(app, client):
    ctx = _setup_org_with_users(app, "sections-complete-ajax")
    _login(client, ctx["admin_email"])
    with app.app_context():
        task = TaskService.create(
            {
                "title": "Ajax complete",
                "due_date": datetime.now(timezone.utc) + timedelta(hours=2),
                "assigned_to": ctx["admin_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        task_id = task.id

    response = client.post(
        f"/tasks/{task_id}/complete",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    with app.app_context():
        refreshed = db.session.get(Task, task_id)
        assert refreshed.status == "completed"


def test_tasks_page_cross_tenant_isolated(app, client):
    ctx = _setup_org_with_users(app, "sections-tenant")
    _login(client, ctx["admin_email"])
    with app.app_context():
        TaskService.create(
            {
                "title": "Visible org task",
                "due_date": datetime.now(timezone.utc) + timedelta(hours=3),
                "assigned_to": ctx["admin_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
        )
        TaskService.create(
            {
                "title": "Other org secret task",
                "due_date": datetime.now(timezone.utc) + timedelta(hours=3),
                "assigned_to": ctx["other_admin_id"],
            },
            ctx["other_org_id"],
            ctx["other_admin_id"],
        )
        db.session.commit()

    response = client.get("/tasks")
    html = response.get_data(as_text=True)
    assert "Visible org task" in html
    assert "Other org secret task" not in html
