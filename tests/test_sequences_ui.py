import json

from app.extensions import db
from app.leads.services import LeadService, get_default_stage
from app.sequences.models import EmailSequenceEnrollment
from app.sequences.services import SequenceService
from app.users.services import create_organization, create_user


def _setup_org(app, slug="seq-ui"):
    with app.app_context():
        org = create_organization(f"Seq UI {slug}", slug)
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
        return {
            "org_id": org.id,
            "other_org_id": other.id,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "other_admin_email": f"admin-other-{slug}@test.com",
            "stage_id": stage.id,
        }


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _create_sequence_with_step(app, org_id, user_id, name="UI Seq"):
    with app.app_context():
        seq = SequenceService.create_sequence(
            {"name": name, "is_active": False, "trigger_type": "manual"},
            org_id,
            user_id,
        )
        SequenceService.add_step(
            seq.id,
            {
                "order_index": 0,
                "subject_template": "Hei {{first_name}}",
                "body_html_template": "<p>{{company}}</p>",
            },
            org_id,
        )
        db.session.commit()
        return seq.id


def test_sequences_list_page_requires_auth(client):
    response = client.get("/sequences")
    assert response.status_code == 401


def test_sequences_list_page_renders(app, client):
    ctx = _setup_org(app, "list-page")
    _create_sequence_with_step(app, ctx["org_id"], ctx["admin_id"])
    _login(client, ctx["admin_email"])
    response = client.get("/sequences")
    assert response.status_code == 200
    assert b"Sekvenssit" in response.data or b"s\xc3\xa4hk\xc3\xb6postisekvenssit" in response.data.lower()
    assert b"UI Seq" in response.data


def test_sequence_builder_page(app, client):
    ctx = _setup_org(app, "builder")
    seq_id = _create_sequence_with_step(app, ctx["org_id"], ctx["admin_id"])
    _login(client, ctx["admin_email"])
    response = client.get(f"/sequences/{seq_id}/builder")
    assert response.status_code == 200
    assert b"Hei" in response.data
    assert b"Lis\xc3\xa4\xc3\xa4 vaihe" in response.data


def test_sequence_stats_page(app, client):
    ctx = _setup_org(app, "stats-page")
    seq_id = _create_sequence_with_step(app, ctx["org_id"], ctx["admin_id"])
    _login(client, ctx["admin_email"])
    response = client.get(f"/sequences/{seq_id}/stats")
    assert response.status_code == 200
    assert b"Ilmoittautuneet" in response.data


def test_sequence_stats_json(app, client):
    ctx = _setup_org(app, "stats-json")
    seq_id = _create_sequence_with_step(app, ctx["org_id"], ctx["admin_id"])
    _login(client, ctx["admin_email"])
    response = client.get(
        f"/sequences/{seq_id}/stats",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["enrolled"] == 0


def test_sequence_preview_endpoint(app, client):
    ctx = _setup_org(app, "preview")
    seq_id = _create_sequence_with_step(app, ctx["org_id"], ctx["admin_id"])
    with app.app_context():
        lead = LeadService.create(
            {"email": "preview@seq.com", "first_name": "Matti", "company": "Oy Ab"},
            ctx["org_id"],
            None,
            actor_role="admin",
        )
        db.session.commit()
        lead_id = lead.id
    _login(client, ctx["admin_email"])
    response = client.post(
        f"/sequences/{seq_id}/preview",
        data=json.dumps({"lead_id": lead_id}),
        content_type="application/json",
        headers={"Accept": "application/json", "X-CSRFToken": "test"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert "Matti" in body["data"]["subject"]
    assert "Oy Ab" in body["data"]["body_html"]


def test_toggle_active_via_json(app, client):
    ctx = _setup_org(app, "toggle")
    seq_id = _create_sequence_with_step(app, ctx["org_id"], ctx["admin_id"])
    _login(client, ctx["admin_email"])
    response = client.put(
        f"/sequences/{seq_id}",
        data=json.dumps({"is_active": True}),
        content_type="application/json",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["is_active"] is True


def test_cross_org_builder_404(app, client):
    ctx = _setup_org(app, "cross-ui")
    seq_id = _create_sequence_with_step(app, ctx["org_id"], ctx["admin_id"])
    _login(client, ctx["other_admin_email"])
    response = client.get(f"/sequences/{seq_id}/builder")
    assert response.status_code == 404


def test_lead_detail_sequences_tab(app, client):
    ctx = _setup_org(app, "lead-tab")
    seq_id = _create_sequence_with_step(app, ctx["org_id"], ctx["admin_id"], name="Lead Tab Seq")
    with app.app_context():
        lead = LeadService.create(
            {"email": "tab@seq.com", "first_name": "Liisa"},
            ctx["org_id"],
            None,
            actor_role="admin",
        )
        db.session.flush()
        lead_id = lead.id
        SequenceService.enroll_lead(lead_id, seq_id, organization_id=ctx["org_id"])
        db.session.commit()
    _login(client, ctx["admin_email"])
    response = client.get(f"/leads/{lead_id}")
    assert response.status_code == 200
    assert b"Sekvenssit" in response.data
    assert b"Lead Tab Seq" in response.data


def test_enroll_lead_json_route(app, client):
    ctx = _setup_org(app, "enroll-json")
    seq_id = _create_sequence_with_step(app, ctx["org_id"], ctx["admin_id"])
    with app.app_context():
        lead = LeadService.create(
            {"email": "enroll@seq.com"},
            ctx["org_id"],
            None,
            actor_role="admin",
        )
        db.session.commit()
        lead_id = lead.id
    _login(client, ctx["admin_email"])
    response = client.post(
        f"/leads/{lead_id}/sequences/enroll",
        data=json.dumps({"sequence_id": seq_id}),
        content_type="application/json",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 201
    with app.app_context():
        assert (
            EmailSequenceEnrollment.query.filter_by(
                lead_id=lead_id, sequence_id=seq_id, status="active"
            ).count()
            == 1
        )
