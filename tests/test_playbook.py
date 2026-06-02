import json

import pytest

from app.extensions import db
from app.leads.models import Lead
from app.leads.playbook import score_bar_class, score_label
from app.leads.services import LeadService
from app.users.services import create_organization, create_user


def _setup_org(app, slug="playbook-org"):
    with app.app_context():
        org = create_organization("Playbook Org", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        other_org = create_organization("Other Org", f"{slug}-other")
        db.session.flush()
        other_admin = create_user(
            f"admin-{slug}-other@test.com",
            "securepassword1",
            role="admin",
            organization_id=other_org.id,
        )
        db.session.commit()
        from app.leads.services import get_default_stage

        stage = get_default_stage(org.id)
        other_stage = get_default_stage(other_org.id)
        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "other_org_id": other_org.id,
            "other_admin_id": other_admin.id,
            "other_admin_email": other_admin.email,
            "stage_id": stage.id,
            "other_stage_id": other_stage.id,
        }


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _create_lead(app, org_id, admin_id, **kwargs):
    with app.app_context():
        data = {
            "email": kwargs.get("email", "lead@example.com"),
            "first_name": kwargs.get("first_name", "Matti"),
            "last_name": kwargs.get("last_name", "Meikäläinen"),
            "company": kwargs.get("company", "Acme Oy"),
            "score": kwargs.get("score"),
        }
        lead = LeadService.create(data, org_id, admin_id, actor_role="admin")
        if kwargs.get("ai_summary"):
            lead.ai_summary = kwargs["ai_summary"]
        if kwargs.get("ai_enrichment_status"):
            lead.ai_enrichment_status = kwargs["ai_enrichment_status"]
        if kwargs.get("ai_enriched") is not None:
            lead.ai_enriched = kwargs["ai_enriched"]
        db.session.commit()
        return lead.id


# --- API endpoint ---


def test_playbook_endpoint_cross_tenant_404(client, app):
    ctx = _setup_org(app, "pb-cross")
    _login(client, ctx["admin_email"])
    other_lead_id = _create_lead(
        app,
        ctx["other_org_id"],
        ctx["other_admin_id"],
        email="other@example.com",
        company="Other Co",
    )

    response = client.get(f"/api/leads/{other_lead_id}/playbook")
    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False


def test_playbook_endpoint_returns_ai_summary(client, app):
    ctx = _setup_org(app, "pb-summary")
    _login(client, ctx["admin_email"])
    summary = "Vahva B2B-prospekti teollisuudessa."
    lead_id = _create_lead(
        app,
        ctx["org_id"],
        ctx["admin_id"],
        ai_summary=summary,
        ai_enrichment_status="completed",
        ai_enriched=True,
        score=75,
    )

    response = client.get(f"/api/leads/{lead_id}/playbook")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["ai_summary"] == summary


def test_playbook_email_template_contains_lead_fields(client, app):
    ctx = _setup_org(app, "pb-email")
    _login(client, ctx["admin_email"])
    lead_id = _create_lead(
        app,
        ctx["org_id"],
        ctx["admin_id"],
        first_name="Liisa",
        company="Nokia Oy",
        ai_enrichment_status="completed",
        ai_enriched=True,
    )

    response = client.get(f"/api/leads/{lead_id}/playbook")
    data = response.get_json()["data"]
    assert "Liisa" in data["email_template"]["body"]
    assert "Nokia Oy" in data["email_template"]["subject"]


def test_playbook_call_script_contains_sender_name(client, app):
    ctx = _setup_org(app, "pb-call")
    _login(client, ctx["admin_email"])
    lead_id = _create_lead(
        app,
        ctx["org_id"],
        ctx["admin_id"],
        ai_enrichment_status="completed",
        ai_enriched=True,
        ai_summary="Kasvava SaaS-yritys.",
    )

    response = client.get(f"/api/leads/{lead_id}/playbook")
    data = response.get_json()["data"]
    sender = ctx["admin_email"].split("@")[0]
    assert sender in data["call_script"]


# --- UI ---


def test_playbook_ui_pending_state(client, app):
    ctx = _setup_org(app, "pb-pending-ui")
    _login(client, ctx["admin_email"])
    lead_id = _create_lead(
        app,
        ctx["org_id"],
        ctx["admin_id"],
        ai_enrichment_status="pending",
    )

    response = client.get(f"/leads/{lead_id}")
    html = response.data.decode()
    assert "AI analysoi liidiä" in html
    assert "playbook-skeleton" in html


@pytest.mark.parametrize(
    "score,expected_class",
    [
        (85, "playbook-score--high"),
        (55, "playbook-score--mid"),
        (25, "playbook-score--low"),
    ],
)
def test_playbook_ui_score_bar_color(client, app, score, expected_class):
    ctx = _setup_org(app, f"pb-score-{score}")
    _login(client, ctx["admin_email"])
    lead_id = _create_lead(
        app,
        ctx["org_id"],
        ctx["admin_id"],
        score=score,
        ai_enrichment_status="completed",
        ai_enriched=True,
    )

    response = client.get(f"/leads/{lead_id}")
    html = response.data.decode()
    assert expected_class in html
    assert score_label(score) in html


def test_playbook_score_helpers():
    assert score_bar_class(75) == "playbook-score--high"
    assert score_bar_class(50) == "playbook-score--mid"
    assert score_bar_class(30) == "playbook-score--low"
    assert score_label(85) == "Korkea osuvuus — toimi nopeasti"
    assert score_label(65) == "Hyvä liidi — seuraa aktiivisesti"
    assert score_label(45) == "Kohtalainen — tarvitsee lisää tietoa"
    assert score_label(30) == "Heikko osuvuus — matala prioriteetti"


def test_playbook_ui_copy_button_present(client, app):
    ctx = _setup_org(app, "pb-copy-ui")
    _login(client, ctx["admin_email"])
    lead_id = _create_lead(
        app,
        ctx["org_id"],
        ctx["admin_id"],
        ai_enrichment_status="completed",
        ai_enriched=True,
        score=70,
    )

    response = client.get(f"/leads/{lead_id}")
    html = response.data.decode()
    assert 'id="playbook-copy-email"' in html
    assert "Kopioi sähköpostipohja" in html
    assert "AI-myyntisuunnitelma" in html
