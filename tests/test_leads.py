import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.extensions import db
from app.leads.models import Activity, Lead, PipelineStage
from app.leads.services import LeadService, LeadServiceError, get_default_stage, seed_default_pipeline_stages
from app.tasks.models import Task
from app.users.models import AuditLog
from app.users.services import create_organization, create_user


@contextmanager
def _capture_query_count():
    count = {"value": 0}

    def before_cursor_execute(*_args, **_kwargs):
        count["value"] += 1

    event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield count
    finally:
        event.remove(db.engine, "before_cursor_execute", before_cursor_execute)


def _setup_org_with_users(app, slug="org-a"):
    with app.app_context():
        org = create_organization("Org A", slug)
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
        other_org = create_organization("Org B", f"{slug}-b")
        db.session.flush()
        other_user = create_user(
            f"user-{slug}-b@acme.com",
            "securepassword1",
            role="user",
            organization_id=other_org.id,
        )
        db.session.commit()
        stage = get_default_stage(org.id)
        other_stage = get_default_stage(other_org.id)
        return {
            "org_id": org.id,
            "admin_email": admin.email,
            "user_email": user.email,
            "admin_id": admin.id,
            "user_id": user.id,
            "stage_id": stage.id,
            "other_org_id": other_org.id,
            "other_user_id": other_user.id,
            "other_stage_id": other_stage.id,
        }


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return response


def _create_lead(app, org_id, stage_id, user_id=None, **kwargs):
    with app.app_context():
        data = {
            "email": kwargs.get("email", "lead@example.com"),
            "company": kwargs.get("company"),
            "first_name": kwargs.get("first_name"),
            "source": kwargs.get("source", "manual"),
            "source_ref": kwargs.get("source_ref"),
            "score": kwargs.get("score"),
            "stage_id": stage_id,
            "assigned_to": kwargs.get("assigned_to"),
        }
        lead = LeadService.create(data, org_id, user_id, actor_role="admin")
        db.session.commit()
        return lead.id


# --- Lead creation ---


def test_create_valid_manual_lead(app):
    ctx = _setup_org_with_users(app)
    with app.app_context():
        lead = LeadService.create(
            {"email": "new@acme.com", "first_name": "Ada"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        assert lead.id is not None
        assert lead.email == "new@acme.com"
        assert Activity.query.filter_by(lead_id=lead.id, type="created").count() == 1
        assert AuditLog.query.filter_by(action="lead_created", target_id=lead.id).count() == 1


def test_create_lead_without_identifier_fails(app):
    ctx = _setup_org_with_users(app)
    with app.app_context():
        with pytest.raises(LeadServiceError) as exc:
            LeadService.create({"notes": "nothing useful"}, ctx["org_id"], ctx["admin_id"])
        assert exc.value.code == "validation_error"


def test_create_lead_invalid_score_fails(app):
    ctx = _setup_org_with_users(app, "score-org")
    with app.app_context():
        with pytest.raises(LeadServiceError):
            LeadService.create({"email": "a@b.com", "score": 101}, ctx["org_id"], ctx["admin_id"])
        with pytest.raises(LeadServiceError):
            LeadService.create({"email": "b@b.com", "score": -1}, ctx["org_id"], ctx["admin_id"])


def test_create_lead_assignee_other_org_fails(app):
    ctx = _setup_org_with_users(app, "assign-org")
    with app.app_context():
        with pytest.raises(LeadServiceError) as exc:
            LeadService.create(
                {"email": "x@y.com", "assigned_to": ctx["other_user_id"]},
                ctx["org_id"],
                ctx["admin_id"],
            )
        assert exc.value.code == "invalid_assignee"


def test_duplicate_source_ref_fails(app):
    ctx = _setup_org_with_users(app, "dup-org")
    with app.app_context():
        LeadService.create(
            {"email": "one@a.com", "source": "n8n", "source_ref": "ref-1"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        with pytest.raises(LeadServiceError) as exc:
            LeadService.create(
                {"email": "two@a.com", "source": "n8n", "source_ref": "ref-1"},
                ctx["org_id"],
                ctx["admin_id"],
            )
        assert exc.value.code == "duplicate_source"


# --- Stage movement ---


def test_move_stage_success(app):
    ctx = _setup_org_with_users(app, "move-org")
    with app.app_context():
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="move@a.com")
        contacted = PipelineStage.query.filter_by(
            organization_id=ctx["org_id"], name="Kontaktoitu"
        ).first()
        LeadService.move_stage(lead_id, contacted.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        lead = db.session.get(Lead, lead_id)
        assert lead.stage_id == contacted.id
        assert lead.stage_changed_at is not None
        assert lead.days_in_current_stage >= 0
        assert Activity.query.filter_by(lead_id=lead_id, type="stage_changed").count() == 1


def test_move_stage_cross_tenant_fails(app):
    ctx = _setup_org_with_users(app, "cross-move")
    with app.app_context():
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="cross@a.com")
        with pytest.raises(LeadServiceError) as exc:
            LeadService.move_stage(lead_id, ctx["other_stage_id"], ctx["org_id"], ctx["admin_id"])
        assert exc.value.code == "invalid_stage"


def test_move_archived_lead_fails(app):
    ctx = _setup_org_with_users(app, "arch-move")
    with app.app_context():
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="arch@a.com")
        LeadService.archive(lead_id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        with pytest.raises(LeadServiceError) as exc:
            LeadService.move_stage(lead_id, ctx["stage_id"], ctx["org_id"], ctx["admin_id"])
        assert exc.value.code == "archived"


def test_move_same_stage_no_duplicate_activity(app):
    ctx = _setup_org_with_users(app, "same-stage")
    with app.app_context():
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="same@a.com")
        LeadService.move_stage(lead_id, ctx["stage_id"], ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        assert Activity.query.filter_by(lead_id=lead_id, type="stage_changed").count() == 0


def test_move_to_won_sets_status(app):
    ctx = _setup_org_with_users(app, "won-org")
    with app.app_context():
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="won@a.com")
        won = PipelineStage.query.filter_by(organization_id=ctx["org_id"], name="Voitettu").first()
        LeadService.move_stage(lead_id, won.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        assert db.session.get(Lead, lead_id).status == "won"


# --- Cross-tenant isolation ---


def test_user_cannot_see_other_org_lead_detail(app, client):
    ctx = _setup_org_with_users(app, "iso-detail")
    lead_id = _create_lead(app, ctx["other_org_id"], ctx["other_stage_id"], email="secret@b.com")
    _login(client, ctx["user_email"])
    response = client.get(f"/leads/{lead_id}")
    assert response.status_code == 404


def test_list_only_own_organization(app, client):
    ctx = _setup_org_with_users(app, "iso-list")
    _create_lead(app, ctx["org_id"], ctx["stage_id"], email="mine@a.com")
    _create_lead(app, ctx["other_org_id"], ctx["other_stage_id"], email="theirs@b.com")
    _login(client, ctx["user_email"])
    response = client.get("/leads")
    assert response.status_code == 200
    assert b"mine@a.com" in response.data
    assert b"theirs@b.com" not in response.data


def test_pipeline_only_own_organization(app, client):
    ctx = _setup_org_with_users(app, "iso-pipe")
    _create_lead(app, ctx["org_id"], ctx["stage_id"], email="pipemine@a.com", company="MineCo")
    _create_lead(app, ctx["other_org_id"], ctx["other_stage_id"], email="pipetheirs@b.com", company="TheirCo")
    _login(client, ctx["user_email"])
    response = client.get("/leads/pipeline")
    assert b"MineCo" in response.data
    assert b"TheirCo" not in response.data


def test_last_activity_column(app, client):
    ctx = _setup_org_with_users(app, "last-activity")
    now = datetime.now(timezone.utc)
    with app.app_context():
        _create_lead(
            app, ctx["org_id"], ctx["stage_id"], email="noactivity@a.com", first_name="No"
        )
        today_id = _create_lead(
            app, ctx["org_id"], ctx["stage_id"], email="today@a.com", first_name="Today"
        )
        old_id = _create_lead(
            app, ctx["org_id"], ctx["stage_id"], email="old@a.com", first_name="Old"
        )
        db.session.add(
            Activity(
                lead_id=today_id,
                user_id=ctx["admin_id"],
                organization_id=ctx["org_id"],
                type="email_sent",
                created_at=now,
            )
        )
        db.session.add(
            Activity(
                lead_id=old_id,
                user_id=ctx["admin_id"],
                organization_id=ctx["org_id"],
                type="call",
                created_at=now - timedelta(days=15),
            )
        )
        # Ensure cross-tenant activity records never affect current-org list logic.
        db.session.add(
            Activity(
                lead_id=today_id,
                user_id=ctx["other_user_id"],
                organization_id=ctx["other_org_id"],
                type="call",
                created_at=now - timedelta(minutes=10),
            )
        )
        db.session.commit()

    _login(client, ctx["admin_email"])
    response = client.get("/leads")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Ei kontaktia" in body
    assert "Sähköposti tänään" in body
    assert "Ei kontaktia 15 pv" in body

    sorted_desc = client.get("/leads?sort=last_activity&order=desc")
    assert sorted_desc.status_code == 200
    desc_body = sorted_desc.data.decode("utf-8")
    assert desc_body.index("today@a.com") < desc_body.index("old@a.com")

    sorted_asc = client.get("/leads?sort=last_activity&order=asc")
    assert sorted_asc.status_code == 200
    asc_body = sorted_asc.data.decode("utf-8")
    assert asc_body.index("noactivity@a.com") < asc_body.index("old@a.com")

    no_contact = client.get("/leads?no_contact_7=1")
    assert no_contact.status_code == 200
    assert b"today@a.com" not in no_contact.data
    assert b"old@a.com" in no_contact.data
    assert b"noactivity@a.com" in no_contact.data


def test_export_only_own_organization(app, client):
    ctx = _setup_org_with_users(app, "iso-export")
    _create_lead(app, ctx["org_id"], ctx["stage_id"], email="exportmine@a.com")
    _create_lead(app, ctx["other_org_id"], ctx["other_stage_id"], email="exporttheirs@b.com")
    _login(client, ctx["admin_email"])
    response = client.get("/leads/export")
    assert response.status_code == 200
    assert b"exportmine@a.com" in response.data
    assert b"exporttheirs@b.com" not in response.data


# --- Permissions ---


def test_normal_user_cannot_archive(app, client):
    ctx = _setup_org_with_users(app, "perm-arch")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="noarch@a.com")
    _login(client, ctx["user_email"])
    response = client.delete(f"/leads/{lead_id}", headers={"Accept": "application/json"})
    assert response.status_code == 403


def test_normal_user_cannot_assign_to_other_user(app):
    ctx = _setup_org_with_users(app, "perm-assign")
    with app.app_context():
        with pytest.raises(LeadServiceError) as exc:
            LeadService.create(
                {"email": "assign@a.com", "assigned_to": ctx["admin_id"]},
                ctx["org_id"],
                ctx["user_id"],
                actor_role="user",
            )
        assert exc.value.code == "forbidden_assign"


def test_admin_can_assign_within_org(app):
    ctx = _setup_org_with_users(app, "perm-admin")
    with app.app_context():
        lead = LeadService.create(
            {"email": "assigned@a.com", "assigned_to": ctx["user_id"]},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        assert lead.assigned_to == ctx["user_id"]


def test_api_client_cannot_access_lead_ui(app, client):
    with app.app_context():
        org = create_organization("API Lead Org", "api-lead-org")
        db.session.flush()
        create_user(
            "api@lead.test",
            "securepassword1",
            role="api_client",
            organization_id=org.id,
        )
        db.session.commit()

    response = client.post(
        "/auth/login",
        data={"email": "api@lead.test", "password": "securepassword1"},
        follow_redirects=True,
    )
    assert b"API" in response.data or b"api" in response.data.lower()

    response = client.get("/leads")
    assert response.status_code in (401, 403, 302)


def test_admin_can_archive(app, client):
    ctx = _setup_org_with_users(app, "admin-arch")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="archok@a.com")
    _login(client, ctx["admin_email"])
    response = client.delete(
        f"/leads/{lead_id}",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    with app.app_context():
        assert db.session.get(Lead, lead_id).status == "archived"


def test_stage_move_json_endpoint(app, client):
    ctx = _setup_org_with_users(app, "json-stage")
    with app.app_context():
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="json@a.com")
        contacted = PipelineStage.query.filter_by(
            organization_id=ctx["org_id"], name="Kontaktoitu"
        ).first()
        contacted_id = contacted.id
    _login(client, ctx["admin_email"])
    response = client.post(
        f"/leads/{lead_id}/stage",
        data=json.dumps({"stage_id": contacted_id}),
        content_type="application/json",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    payload = json.loads(response.data)
    assert payload["success"] is True
    assert payload["data"]["stage_id"] == contacted_id


def test_pipeline_card_data(app):
    ctx = _setup_org_with_users(app, "pipe-card")
    with app.app_context():
        lead_id = _create_lead(
            app,
            ctx["org_id"],
            ctx["stage_id"],
            email="card@a.com",
            company="CardCo",
            score=82,
        )
        other_lead_id = _create_lead(
            app,
            ctx["other_org_id"],
            ctx["other_stage_id"],
            email="other-card@b.com",
            company="OtherCardCo",
            score=50,
        )
        lead = db.session.get(Lead, lead_id)
        two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
        five_days_ago = datetime.now(timezone.utc) - timedelta(days=5)
        db.session.add(
            Activity(
                lead_id=lead.id,
                user_id=ctx["admin_id"],
                organization_id=ctx["org_id"],
                type="note",
                content="Follow-up note",
                created_at=two_days_ago,
            )
        )
        db.session.add(
            Activity(
                lead_id=lead.id,
                user_id=ctx["admin_id"],
                organization_id=ctx["org_id"],
                type="stage_changed",
                metadata_json={"new_stage_id": lead.stage_id},
                created_at=five_days_ago,
            )
        )
        db.session.add(
            Task(
                organization_id=ctx["org_id"],
                lead_id=lead.id,
                assigned_to=ctx["admin_id"],
                title="Soita asiakkaalle",
                status="pending",
                type="call",
                priority="normal",
                due_date=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db.session.add(
            Task(
                organization_id=ctx["other_org_id"],
                lead_id=other_lead_id,
                assigned_to=ctx["other_user_id"],
                title="Other task",
                status="pending",
                type="call",
                priority="normal",
                due_date=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        db.session.commit()

        with _capture_query_count() as query_counter:
            data = LeadService.get_pipeline_data(ctx["org_id"], {})
        assert query_counter["value"] <= 12
        stage_items = data["leads_by_stage"].get(ctx["stage_id"], [])
        mine = next(item for item in stage_items if item["lead"].id == lead_id)
        assert mine["days_since_last_activity"] == 0
        assert mine["stage_days"] == lead.days_in_current_stage
        assert mine["next_task"] is not None
        assert mine["next_task"].lead_id == lead_id
        all_emails = {
            entry["lead"].email
            for entries in data["leads_by_stage"].values()
            for entry in entries
        }
        assert "other-card@b.com" not in all_emails


def test_lost_reason_required(app, client):
    ctx = _setup_org_with_users(app, "lost-required")
    with app.app_context():
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="lost@a.com")
        lost_stage = PipelineStage.query.filter_by(
            organization_id=ctx["org_id"], name="Hävitty"
        ).first()
        lost_stage_id = lost_stage.id
        contacted = PipelineStage.query.filter_by(
            organization_id=ctx["org_id"], name="Kontaktoitu"
        ).first()
        contacted_id = contacted.id
    _login(client, ctx["admin_email"])

    missing_reason = client.post(
        f"/leads/{lead_id}/stage",
        data=json.dumps({"stage_id": lost_stage_id}),
        content_type="application/json",
        headers={"Accept": "application/json"},
    )
    assert missing_reason.status_code == 400

    ok = client.post(
        f"/leads/{lead_id}/stage",
        data=json.dumps(
            {
                "stage_id": lost_stage_id,
                "lost_reason": "Ei vastannut",
                "lost_reason_note": "",
            }
        ),
        content_type="application/json",
        headers={"Accept": "application/json"},
    )
    assert ok.status_code == 200
    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert lead.lost_reason == "no_response"

    back_to_contacted = client.post(
        f"/leads/{lead_id}/stage",
        data=json.dumps({"stage_id": contacted_id}),
        content_type="application/json",
        headers={"Accept": "application/json"},
    )
    assert back_to_contacted.status_code == 200


def test_web_api_lead_stage_patch(app, client):
    ctx = _setup_org_with_users(app, "web-api-stage")
    with app.app_context():
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="webapi@a.com")
        lost_stage = PipelineStage.query.filter_by(
            organization_id=ctx["org_id"], name="Hävitty"
        ).first()
        lost_stage_id = lost_stage.id
    _login(client, ctx["admin_email"])

    missing = client.patch(
        f"/api/leads/{lead_id}/stage",
        data=json.dumps({"stage_id": lost_stage_id}),
        content_type="application/json",
        headers={"Accept": "application/json"},
    )
    assert missing.status_code == 400
    assert missing.get_json()["success"] is False

    ok = client.patch(
        f"/api/leads/{lead_id}/stage",
        data=json.dumps(
            {
                "stage_id": lost_stage_id,
                "lost_reason": "no_budget",
                "lost_reason_note": "Q2 budjetti käytetty",
            }
        ),
        content_type="application/json",
        headers={"Accept": "application/json"},
    )
    assert ok.status_code == 200
    payload = ok.get_json()
    assert payload["success"] is True
    assert payload["data"]["stage"] == "Hävitty"
    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert lead.lost_reason == "no_budget"
        assert lead.lost_reason_note == "Q2 budjetti käytetty"


def test_seed_default_stages_idempotent(app):
    with app.app_context():
        org = create_organization("Seed Co", "seed-co")
        db.session.commit()
        count1 = PipelineStage.query.filter_by(organization_id=org.id).count()
        seed_default_pipeline_stages(org.id)
        db.session.commit()
        count2 = PipelineStage.query.filter_by(organization_id=org.id).count()
        assert count1 == 6
        assert count2 == 6


def test_days_in_current_stage_uses_stage_changed_at(app):
    ctx = _setup_org_with_users(app, "stage-days")
    with app.app_context():
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="days@a.com")
        lead = db.session.get(Lead, lead_id)
        lead.created_at = datetime.now(timezone.utc) - timedelta(days=30)
        lead.stage_changed_at = datetime.now(timezone.utc) - timedelta(days=4)
        db.session.commit()
        assert lead.days_in_current_stage == 4


def test_pipeline_contains_lost_reason_modal(app, client):
    ctx = _setup_org_with_users(app, "lost-modal")
    _create_lead(app, ctx["org_id"], ctx["stage_id"], email="modal@a.com", company="ModalCo")
    _login(client, ctx["admin_email"])
    response = client.get("/leads/pipeline")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'id="lost-reason-modal"' in body
    assert 'id="lost-reason-select"' in body
    assert 'value="no_response"' in body
    assert 'id="lost-confirm"' in body
    assert 'data-lead-id="' in body
    assert 'sortable-lead' in body
    assert 'data-id="' in body
    assert "buildStageUrl" in body
    assert "parseLeadId" in body
    assert "resolveLeadCardElement" in body
    assert "readLeadIdFromCard" in body
    assert "OPEN LOST MODAL WITH STATE" in body
    assert "resolveLostConfirmLeadId" in body


def test_undefined_lead_stage_url_returns_flask_404_not_route_match(app, client):
    """PATCH /leads/undefined/stage 404s because <int:lead_id> does not match — not missing blueprint."""
    ctx = _setup_org_with_users(app, "undef-stage")
    _login(client, ctx["admin_email"])
    response = client.patch(
        "/leads/undefined/stage",
        data=json.dumps({"stage_id": 1, "lost_reason": "no_budget"}),
        content_type="application/json",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["error"]["message"] == "The requested resource was not found."
