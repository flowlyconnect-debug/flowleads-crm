import json

import pytest

from app.extensions import db
from app.leads.models import Activity, Lead, PipelineStage
from app.leads.services import LeadService, LeadServiceError, get_default_stage, seed_default_pipeline_stages
from app.users.models import AuditLog
from app.users.services import create_organization, create_user


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
            organization_id=ctx["org_id"], name="Contacted"
        ).first()
        LeadService.move_stage(lead_id, contacted.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        lead = db.session.get(Lead, lead_id)
        assert lead.stage_id == contacted.id
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
        won = PipelineStage.query.filter_by(organization_id=ctx["org_id"], name="Won").first()
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
            organization_id=ctx["org_id"], name="Contacted"
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
