from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.leads.models import Lead
from app.leads.services import LeadService, get_default_stage
from app.tasks.models import Task
from app.tasks.services import TaskService
from app.users.services import create_organization, create_user


def _setup_org(app, slug="dash-today"):
    with app.app_context():
        org = create_organization("Today Org", slug)
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
        other = create_organization("Other Today Org", f"{slug}-other")
        db.session.flush()
        other_user = create_user(
            f"other-{slug}@test.com",
            "securepassword1",
            role="user",
            organization_id=other.id,
        )
        db.session.commit()
        stage = get_default_stage(org.id)
        other_stage = get_default_stage(other.id)
        return {
            "org_id": org.id,
            "other_org_id": other.id,
            "admin_id": admin.id,
            "user_id": user.id,
            "other_user_id": other_user.id,
            "admin_email": admin.email,
            "user_email": user.email,
            "stage_id": stage.id,
            "other_stage_id": other_stage.id,
        }


def _login(client, email):
    resp = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def _create_lead_without_activities(app, ctx, email, **kwargs):
    with app.app_context():
        lead = Lead(
            organization_id=ctx["org_id"],
            stage_id=ctx["stage_id"],
            status="active",
            source=kwargs.get("source", "manual"),
            email=email,
            first_name=kwargs.get("first_name", "Test"),
            last_name=kwargs.get("last_name", "Lead"),
            company=kwargs.get("company", "Test Oy"),
            score=kwargs.get("score"),
            last_contacted_at=kwargs.get("last_contacted_at"),
            created_at=kwargs.get("created_at") or datetime.now(timezone.utc),
        )
        db.session.add(lead)
        db.session.commit()
        return lead.id


def test_today_endpoint_cross_tenant_isolation(client, app):
    ctx = _setup_org(app, "today-x")
    with app.app_context():
        hot = LeadService.create(
            {"email": "hot@test.com", "score": 85, "company": "Mine"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        hot_other = LeadService.create(
            {"email": "hot-other@test.com", "score": 95, "company": "Theirs"},
            ctx["other_org_id"],
            ctx["other_user_id"],
        )
        db.session.commit()

        due = datetime.now(timezone.utc) - timedelta(days=1)
        mine_task = TaskService.create(
            {"title": "Mine overdue", "due_date": due, "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=hot.id,
        )
        other_task = TaskService.create(
            {"title": "Other overdue", "due_date": due, "assigned_to": ctx["other_user_id"]},
            ctx["other_org_id"],
            ctx["other_user_id"],
            lead_id=hot_other.id,
        )
        db.session.commit()
        mine_task_id = mine_task.id
        other_task_id = other_task.id
        hot_id = hot.id
        hot_other_id = hot_other.id

    _login(client, ctx["user_email"])
    resp = client.get("/api/dashboard/today")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    payload = data["data"]

    hot_ids = {item["id"] for item in payload["hot_leads"]}
    assert hot_id in hot_ids
    assert hot_other_id not in hot_ids

    task_ids = {item["id"] for item in payload["overdue_tasks"]}
    assert mine_task_id in task_ids
    assert other_task_id not in task_ids


def test_today_hot_leads_score_threshold(client, app):
    ctx = _setup_org(app, "today-hot")
    with app.app_context():
        low = LeadService.create(
            {"email": "low@test.com", "score": 50},
            ctx["org_id"],
            ctx["admin_id"],
        )
        high = LeadService.create(
            {"email": "high@test.com", "score": 75},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        low_id = low.id
        high_id = high.id

    _login(client, ctx["user_email"])
    resp = client.get("/api/dashboard/today")
    payload = resp.get_json()["data"]
    ids = {item["id"] for item in payload["hot_leads"]}
    assert high_id in ids
    assert low_id not in ids
    for item in payload["hot_leads"]:
        assert item["score"] >= 70


def test_today_unprocessed_only_without_activities(client, app):
    ctx = _setup_org(app, "today-unproc")
    with app.app_context():
        settings_off = LeadService.create(
            {"email": "with-act@test.com"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        unprocessed_id = _create_lead_without_activities(
            app,
            ctx,
            "fresh@test.com",
            company="Fresh Oy",
            source="import",
        )
        processed_id = settings_off.id

    _login(client, ctx["user_email"])
    resp = client.get("/api/dashboard/today")
    payload = resp.get_json()["data"]
    ids = {item["id"] for item in payload["unprocessed_leads"]}
    assert unprocessed_id in ids
    assert processed_id not in ids


def test_today_overdue_tasks_filter(client, app):
    ctx = _setup_org(app, "today-overdue")
    now = datetime.now(timezone.utc)
    with app.app_context():
        lead = LeadService.create(
            {"email": "task-lead@test.com"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        overdue = TaskService.create(
            {
                "title": "Late follow-up",
                "due_date": now - timedelta(days=2),
                "assigned_to": ctx["user_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=lead.id,
        )
        future = TaskService.create(
            {
                "title": "Future task",
                "due_date": now + timedelta(days=3),
                "assigned_to": ctx["user_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=lead.id,
        )
        done = TaskService.create(
            {
                "title": "Already done",
                "due_date": now - timedelta(days=1),
                "assigned_to": ctx["user_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=lead.id,
        )
        db.session.commit()
        TaskService.complete(done.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        overdue_id = overdue.id
        future_id = future.id
        done_id = done.id

    _login(client, ctx["user_email"])
    resp = client.get("/api/dashboard/today")
    payload = resp.get_json()["data"]
    ids = {item["id"] for item in payload["overdue_tasks"]}
    assert overdue_id in ids
    assert future_id not in ids
    assert done_id not in ids


def test_ai_worklist_ranking_overdue_before_warm(client, app):
    ctx = _setup_org(app, "today-rank")
    now = datetime.now(timezone.utc)
    with app.app_context():
        warm_lead = Lead(
            organization_id=ctx["org_id"],
            stage_id=ctx["stage_id"],
            status="active",
            source="manual",
            email="warm@test.com",
            first_name="Warm",
            last_name="Lead",
            company="Warm Oy",
            score=65,
            last_contacted_at=now - timedelta(days=20),
        )
        db.session.add(warm_lead)
        db.session.flush()
        hot_lead = LeadService.create(
            {"email": "hot-rank@test.com", "score": 85, "company": "Hot Oy"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        overdue = TaskService.create(
            {
                "title": "Very late",
                "due_date": now - timedelta(days=4),
                "assigned_to": ctx["user_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=hot_lead.id,
        )
        db.session.commit()
        overdue_id = overdue.id

    _login(client, ctx["user_email"])
    resp = client.get("/api/dashboard/ai-worklist")
    payload = resp.get_json()["data"]["items"]
    assert payload
    assert payload[0]["kind"] == "overdue_task"
    assert "Follow-up" in payload[0]["reason"]

    kinds = [item["kind"] for item in payload]
    if "warm_lead" in kinds and "overdue_task" in kinds:
        assert kinds.index("overdue_task") < kinds.index("warm_lead")


def test_ai_worklist_cross_tenant(client, app):
    ctx = _setup_org(app, "today-wl-x")
    now = datetime.now(timezone.utc)
    with app.app_context():
        other_lead = LeadService.create(
            {"email": "other-wl@test.com", "score": 90},
            ctx["other_org_id"],
            ctx["other_user_id"],
        )
        db.session.commit()
        TaskService.create(
            {
                "title": "Other org task",
                "due_date": now - timedelta(days=5),
                "assigned_to": ctx["other_user_id"],
            },
            ctx["other_org_id"],
            ctx["other_user_id"],
            lead_id=other_lead.id,
        )
        db.session.commit()

    _login(client, ctx["user_email"])
    resp = client.get("/api/dashboard/ai-worklist")
    payload = resp.get_json()["data"]["items"]
    for item in payload:
        assert "Other org task" not in item["action_text"]


def test_dashboard_metrics(client, app):
    ctx = _setup_org(app, "dash-metrics")
    now = datetime.now(timezone.utc)
    with app.app_context():
        # This org: 3 leads in current 7d, 1 lead in previous 7d
        current_a = _create_lead_without_activities(
            app,
            ctx,
            "metric-a@test.com",
            score=72,
            created_at=now - timedelta(days=2),
            company="Current A",
        )
        current_b = _create_lead_without_activities(
            app,
            ctx,
            "metric-b@test.com",
            score=40,
            created_at=now - timedelta(days=3),
            company="Current B",
        )
        current_c = _create_lead_without_activities(
            app,
            ctx,
            "metric-c@test.com",
            score=85,
            created_at=now - timedelta(hours=5),
            company="Current C",
        )
        _create_lead_without_activities(
            app,
            ctx,
            "metric-prev@test.com",
            score=75,
            created_at=now - timedelta(days=10),
            company="Previous",
        )

        # Other org should never affect counts
        other_stage_id = ctx["other_stage_id"]
        other_lead = Lead(
            organization_id=ctx["other_org_id"],
            stage_id=other_stage_id,
            status="active",
            source="manual",
            email="other-metric@test.com",
            first_name="Other",
            last_name="Metric",
            score=99,
            created_at=now - timedelta(days=1),
            deal_value=20000,
        )
        db.session.add(other_lead)

        # Tasks: one due today + one overdue in this org
        due_today = now.replace(hour=15, minute=0, second=0, microsecond=0)
        TaskService.create(
            {"title": "Today task", "due_date": due_today, "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=current_a,
        )
        TaskService.create(
            {"title": "Overdue task", "due_date": now - timedelta(days=2), "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=current_b,
        )
        TaskService.create(
            {"title": "Other org today", "due_date": due_today, "assigned_to": ctx["other_user_id"]},
            ctx["other_org_id"],
            ctx["other_user_id"],
            lead_id=other_lead.id,
        )

        # Pipeline value should sum only open leads in current org
        lead_a = Lead.query.get(current_a)
        lead_b = Lead.query.get(current_b)
        lead_c = Lead.query.get(current_c)
        lead_a.deal_value = 1000
        lead_b.deal_value = 3000
        lead_c.deal_value = None
        db.session.commit()

    _login(client, ctx["user_email"])
    resp = client.get("/api/dashboard/metrics")
    assert resp.status_code == 200
    payload = resp.get_json()["data"]

    assert payload["new_leads_7d"] == 3
    assert payload["hot_leads"] == 3
    assert payload["tasks_today"] == 1
    assert payload["pipeline_value"] == 4000.0


def test_ai_worklist(client, app):
    ctx = _setup_org(app, "dash-worklist")
    now = datetime.now(timezone.utc)
    with app.app_context():
        hot = _create_lead_without_activities(
            app,
            ctx,
            "hot-wl@test.com",
            score=84,
            created_at=now - timedelta(days=8),
            last_contacted_at=now - timedelta(days=9),
            company="Revealuxury",
        )
        warm = _create_lead_without_activities(
            app,
            ctx,
            "warm-wl@test.com",
            score=65,
            created_at=now - timedelta(days=20),
            last_contacted_at=now - timedelta(days=15),
            company="Warm Oy",
        )
        new_lead = _create_lead_without_activities(
            app,
            ctx,
            "new-wl@test.com",
            score=71,
            created_at=now - timedelta(hours=6),
            company="New Oy",
        )

        TaskService.create(
            {"title": "Follow-up A", "due_date": now - timedelta(days=5), "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=hot,
        )
        TaskService.create(
            {"title": "Follow-up B", "due_date": now - timedelta(days=6), "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=warm,
        )
        TaskService.create(
            {"title": "Follow-up C", "due_date": now - timedelta(days=7), "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
            lead_id=new_lead,
        )
        # Other org item must not appear
        other_lead = LeadService.create(
            {"email": "other-worklist@test.com", "score": 90},
            ctx["other_org_id"],
            ctx["other_user_id"],
        )
        TaskService.create(
            {"title": "Other org task", "due_date": now - timedelta(days=8), "assigned_to": ctx["other_user_id"]},
            ctx["other_org_id"],
            ctx["other_user_id"],
            lead_id=other_lead.id,
        )
        db.session.commit()

    _login(client, ctx["user_email"])
    resp = client.get("/api/dashboard/ai-worklist")
    assert resp.status_code == 200
    payload = resp.get_json()["data"]["items"]

    assert isinstance(payload, list)
    assert len(payload) <= 5
    for item in payload:
        assert "Other org task" not in item["action_text"]

    # Empty list should be [] not null
    with app.app_context():
        Task.query.filter(Task.organization_id == ctx["org_id"]).delete()
        Lead.query.filter(Lead.organization_id == ctx["org_id"]).delete()
        db.session.commit()
    resp_empty = client.get("/api/dashboard/ai-worklist")
    assert resp_empty.status_code == 200
    assert resp_empty.get_json()["data"]["items"] == []
