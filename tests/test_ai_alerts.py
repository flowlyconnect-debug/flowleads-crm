from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService, get_default_stage
from app.tasks.models import Task
from app.users.services import create_organization, create_user


def _setup_org(app, slug: str = "alerts"):
    with app.app_context():
        org = create_organization(f"Org {slug}", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        other_org = create_organization(f"Other {slug}", f"{slug}-other")
        db.session.flush()
        other_admin = create_user(
            f"admin-{slug}-other@test.com",
            "securepassword1",
            role="admin",
            organization_id=other_org.id,
        )
        stage = get_default_stage(org.id)
        other_stage = get_default_stage(other_org.id)
        db.session.commit()
        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "stage_id": stage.id,
            "other_org_id": other_org.id,
            "other_admin_email": other_admin.email,
            "other_stage_id": other_stage.id,
        }


def _login(client, email: str):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _create_lead(app, org_id: int, stage_id: int, **kwargs) -> int:
    with app.app_context():
        lead = LeadService.create(
            {
                "email": kwargs.get("email", f"lead-{datetime.now(timezone.utc).timestamp()}@test.com"),
                "company": kwargs.get("company", "Acme Oy"),
                "first_name": kwargs.get("first_name", "Testi"),
                "last_name": kwargs.get("last_name", "Kayttaja"),
                "stage_id": stage_id,
                "score": kwargs.get("score"),
            },
            org_id,
            None,
            actor_role="admin",
        )
        lead.last_contacted_at = kwargs.get("last_contacted_at")
        if kwargs.get("created_at") is not None:
            lead.created_at = kwargs["created_at"]
        db.session.commit()
        return lead.id


def test_ai_alerts_only_current_org(client, app):
    ctx = _setup_org(app, "alerts-scope")
    _login(client, ctx["admin_email"])

    mine = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="mine-alert@test.com")
    _create_lead(
        app,
        ctx["other_org_id"],
        ctx["other_stage_id"],
        email="other-alert@test.com",
    )
    with app.app_context():
        task = Task(
            organization_id=ctx["org_id"],
            lead_id=mine,
            assigned_to=ctx["admin_id"],
            title="Overdue mine",
            type="follow_up",
            priority="normal",
            status="pending",
            due_date=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.session.add(task)
        db.session.commit()

    response = client.get("/api/ai/alerts")
    assert response.status_code == 200
    alerts = response.get_json()["data"]["alerts"]
    assert all(item.get("lead_name") != "other-alert@test.com" for item in alerts)


def test_ai_alerts_prioritize_overdue_before_hot(client, app):
    ctx = _setup_org(app, "alerts-priority")
    _login(client, ctx["admin_email"])
    overdue_lead = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="overdue@test.com")
    hot_lead = _create_lead(
        app,
        ctx["org_id"],
        ctx["stage_id"],
        email="hot@test.com",
        score=84,
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=9),
    )
    with app.app_context():
        task = Task(
            organization_id=ctx["org_id"],
            lead_id=overdue_lead,
            assigned_to=ctx["admin_id"],
            title="Late task",
            type="follow_up",
            priority="high",
            status="pending",
            due_date=datetime.now(timezone.utc) - timedelta(days=2),
        )
        db.session.add(task)
        Activity.query.filter_by(lead_id=hot_lead, type="created").delete()
        db.session.commit()

    response = client.get("/api/ai/alerts")
    alerts = response.get_json()["data"]["alerts"]
    assert alerts[0]["type"] == "overdue_task"
    assert any(item["type"] == "hot_lead_no_contact" for item in alerts)


def test_ai_alerts_max_five(client, app):
    ctx = _setup_org(app, "alerts-max")
    _login(client, ctx["admin_email"])
    for i in range(8):
        lead_id = _create_lead(
            app,
            ctx["org_id"],
            ctx["stage_id"],
            email=f"max-{i}@test.com",
        )
        with app.app_context():
            db.session.add(
                Task(
                    organization_id=ctx["org_id"],
                    lead_id=lead_id,
                    assigned_to=ctx["admin_id"],
                    title=f"Task {i}",
                    type="follow_up",
                    priority="normal",
                    status="pending",
                    due_date=datetime.now(timezone.utc) - timedelta(days=1, minutes=i),
                )
            )
            db.session.commit()

    response = client.get("/api/ai/alerts")
    alerts = response.get_json()["data"]["alerts"]
    assert len(alerts) == 5


def test_ai_alerts_empty_array_when_no_alerts(client, app):
    ctx = _setup_org(app, "alerts-empty")
    _login(client, ctx["admin_email"])
    response = client.get("/api/ai/alerts")
    assert response.status_code == 200
    alerts = response.get_json()["data"]["alerts"]
    assert alerts == []


def test_complete_task_removes_alert_on_next_fetch(client, app):
    ctx = _setup_org(app, "alerts-complete")
    _login(client, ctx["admin_email"])
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="complete@test.com")
    with app.app_context():
        task = Task(
            organization_id=ctx["org_id"],
            lead_id=lead_id,
            assigned_to=ctx["admin_id"],
            title="Need completing",
            type="follow_up",
            priority="high",
            status="pending",
            due_date=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.session.add(task)
        db.session.commit()
        task_id = task.id

    first = client.get("/api/ai/alerts")
    first_alerts = first.get_json()["data"]["alerts"]
    assert any(item.get("task_id") == task_id for item in first_alerts)

    done = client.patch(
        f"/tasks/{task_id}/complete",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert done.status_code == 200

    second = client.get("/api/ai/alerts")
    second_alerts = second.get_json()["data"]["alerts"]
    assert all(item.get("task_id") != task_id for item in second_alerts)
