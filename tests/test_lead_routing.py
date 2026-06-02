from datetime import datetime, timedelta, timezone

from app.ai.services import apply_score_tags
from app.extensions import db
from app.leads.models import Lead, PipelineStage
from app.streams.models import OrgLeadSettings
from app.streams.services import LeadHealthService, LeadRoutingService
from app.users.services import create_organization, create_user


def _setup_org(slug: str):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    stages = (
        PipelineStage.query.filter_by(organization_id=org.id)
        .order_by(PipelineStage.order_index.asc())
        .all()
    )
    admin = create_user(
        f"admin-{slug}@example.com",
        "securepassword1",
        role="admin",
        organization_id=org.id,
    )
    user = create_user(
        f"user-{slug}@example.com",
        "securepassword1",
        role="user",
        organization_id=org.id,
    )
    db.session.flush()
    return org, stages, admin, user


def _make_lead(org_id: int, stage_id: int, **kwargs) -> Lead:
    lead = Lead(
        organization_id=org_id,
        stage_id=stage_id,
        source="n8n",
        status="active",
        email=kwargs.get("email", f"lead-{org_id}-{stage_id}@example.com"),
        assigned_to=kwargs.get("assigned_to"),
        tags=kwargs.get("tags", []),
    )
    db.session.add(lead)
    db.session.flush()
    return lead


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_apply_routing_sets_stage(app):
    with app.app_context():
        org, stages, _admin, _user = _setup_org("routing-stage")
        settings = LeadRoutingService.get_settings(org.id)
        settings.default_pipeline_stage_id = stages[1].id
        lead = _make_lead(org.id, stages[0].id)
        lead.stage_id = None
        LeadRoutingService.apply_to_lead(lead, settings)
        assert lead.stage_id == stages[1].id

        existing = _make_lead(org.id, stages[2].id, email="existing-stage@example.com")
        LeadRoutingService.apply_to_lead(existing, settings)
        assert existing.stage_id == stages[2].id


def test_apply_routing_sets_owner(app):
    with app.app_context():
        org, stages, _admin, user = _setup_org("routing-owner")
        settings = LeadRoutingService.get_settings(org.id)
        settings.default_owner_id = user.id
        lead = _make_lead(org.id, stages[0].id)
        lead.assigned_to = None
        LeadRoutingService.apply_to_lead(lead, settings)
        assert lead.assigned_to == user.id

        existing = _make_lead(
            org.id, stages[0].id, email="existing-owner@example.com", assigned_to=user.id
        )
        LeadRoutingService.apply_to_lead(existing, settings)
        assert existing.assigned_to == user.id


def test_apply_routing_merges_tags(app):
    with app.app_context():
        org, stages, _admin, _user = _setup_org("routing-tags")
        settings = LeadRoutingService.get_settings(org.id)
        settings.default_tags = ["saas"]
        lead = _make_lead(org.id, stages[0].id, tags=["b2b"])
        LeadRoutingService.apply_to_lead(lead, settings)
        assert set(lead.tags) == {"b2b", "saas"}
        LeadRoutingService.apply_to_lead(lead, settings)
        assert lead.tags.count("saas") == 1


def test_fallback_stage(app):
    with app.app_context():
        org, stages, _admin, _user = _setup_org("routing-fallback")
        settings = LeadRoutingService.get_settings(org.id)
        settings.default_pipeline_stage_id = None
        lead = _make_lead(org.id, stages[0].id, email="fallback@example.com")
        fallback = LeadRoutingService.get_fallback_stage(org.id)
        if fallback:
            lead.stage_id = fallback.id
        assert lead.stage_id == stages[0].id

        empty_org = create_organization("No stages", "routing-no-stages")
        db.session.flush()
        PipelineStage.query.filter_by(organization_id=empty_org.id).delete()
        db.session.flush()
        fallback_none = LeadRoutingService.get_fallback_stage(empty_org.id)
        assert fallback_none is None


def test_lead_settings_page_get_and_post(client, app):
    with app.app_context():
        org, stages, admin, _user = _setup_org("routing-ui")
        org_id = org.id
        admin_email = admin.email
        stage_id = stages[0].id
        db.session.commit()

    _login(client, admin_email)
    response = client.get("/settings/leads")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Liidiasetukset" in html
    assert 'action="/settings/leads"' in html or "action=\"/settings/leads\"" in html

    save_resp = client.post(
        "/settings/leads",
        data={
            "default_pipeline_stage_id": str(stage_id),
            "default_owner_id": "",
            "default_tags": "b2b, saas",
            "default_industry": "SaaS",
            "default_region": "Uusimaa",
        },
        follow_redirects=True,
    )
    assert save_resp.status_code == 200
    assert "Asetukset tallennettu" in save_resp.get_data(as_text=True)

    with app.app_context():
        settings = LeadRoutingService.get_settings(org_id)
        assert settings.default_pipeline_stage_id == stage_id
        assert settings.default_tags == ["b2b", "saas"]
        assert settings.default_industry == "SaaS"
        assert settings.default_region == "Uusimaa"


def test_settings_auto_created(app):
    with app.app_context():
        org = create_organization("Auto settings", "routing-auto-settings")
        db.session.flush()
        OrgLeadSettings.query.filter_by(organization_id=org.id).delete()
        db.session.flush()
        settings = LeadRoutingService.get_settings(org.id)
        assert settings is not None
        assert settings.organization_id == org.id


def test_score_tagging(app):
    with app.app_context():
        org, stages, _admin, _user = _setup_org("routing-score")
        lead = _make_lead(org.id, stages[0].id, tags=[])
        for score, expected in ((85, "hot"), (65, "warm"), (20, "cold")):
            lead.score = score
            apply_score_tags(lead)
            assert expected in lead.tags
        before = list(lead.tags)
        apply_score_tags(lead)
        assert before == lead.tags


def test_cross_tenant_settings_update_validation(client, app):
    with app.app_context():
        org_a, stages_a, _admin_a, _user_a = _setup_org("routing-cross-a")
        org_b, stages_b, admin_b, _user_b = _setup_org("routing-cross-b")
        admin_b_email = admin_b.email
        org_b_id = org_b.id
        foreign_stage_id = stages_a[0].id
        local_stage_id = stages_b[0].id
        foreign_owner_id = _admin_a.id
        db.session.commit()

    _login(client, admin_b_email)
    response = client.post(
        "/settings/leads",
        data={
            "default_pipeline_stage_id": foreign_stage_id,
            "default_owner_id": "",
            "default_tags": "x",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Virheellinen vaihe" in response.get_data(as_text=True)

    response2 = client.post(
        "/settings/leads",
        data={
            "default_pipeline_stage_id": local_stage_id,
            "default_owner_id": foreign_owner_id,
            "default_tags": "x",
        },
        follow_redirects=True,
    )
    assert response2.status_code == 200
    assert "Virheellinen omistaja" in response2.get_data(as_text=True)


def test_cross_tenant_routing_isolated(app):
    with app.app_context():
        org_a, stages_a, _admin_a, user_a = _setup_org("routing-isolated-a")
        org_b, stages_b, _admin_b, _user_b = _setup_org("routing-isolated-b")
        settings_a = LeadRoutingService.get_settings(org_a.id)
        settings_b = LeadRoutingService.get_settings(org_b.id)
        settings_a.default_pipeline_stage_id = stages_a[1].id
        settings_a.default_owner_id = user_a.id
        settings_b.default_pipeline_stage_id = stages_b[0].id
        settings_b.default_owner_id = None
        lead_b = _make_lead(org_b.id, stages_b[0].id)
        lead_b.assigned_to = None
        LeadRoutingService.apply_to_lead(lead_b, settings_b)
        assert lead_b.stage_id == stages_b[0].id
        assert lead_b.assigned_to is None


def test_health_monitoring(app):
    with app.app_context():
        old_org, _stages, _admin, _user = _setup_org("routing-health-old")
        fresh_org, _stages2, _admin2, _user2 = _setup_org("routing-health-fresh")
        zero_org, _stages3, _admin3, _user3 = _setup_org("routing-health-zero")
        old = LeadRoutingService.get_settings(old_org.id)
        fresh = LeadRoutingService.get_settings(fresh_org.id)
        zero = LeadRoutingService.get_settings(zero_org.id)
        old.last_lead_at = datetime.now(timezone.utc) - timedelta(days=4)
        old.total_lead_count = 3
        fresh.last_lead_at = datetime.now(timezone.utc)
        fresh.total_lead_count = 4
        zero.last_lead_at = datetime.now(timezone.utc) - timedelta(days=10)
        zero.total_lead_count = 0
        db.session.flush()

        stale = LeadHealthService.get_stale_org_settings()
        org_ids = {item.organization_id for item in stale}
        assert old_org.id in org_ids
        assert fresh_org.id not in org_ids
        assert zero_org.id not in org_ids


def test_apply_routing_sets_industry_region(app):
    with app.app_context():
        org, stages, _admin, _user = _setup_org("routing-industry-region")
        settings = LeadRoutingService.get_settings(org.id)
        settings.default_industry = "SaaS"
        settings.default_region = "Uusimaa"
        lead = _make_lead(org.id, stages[0].id, email="industry-region@example.com")
        lead.industry = None
        lead.region = None
        LeadRoutingService.apply_to_lead(lead, settings)
        assert lead.industry == "SaaS"
        assert lead.region == "Uusimaa"

        lead_with_values = _make_lead(
            org.id, stages[0].id, email="industry-region-existing@example.com"
        )
        lead_with_values.industry = "Rakentaminen"
        lead_with_values.region = "Pirkanmaa"
        LeadRoutingService.apply_to_lead(lead_with_values, settings)
        assert lead_with_values.industry == "Rakentaminen"
        assert lead_with_values.region == "Pirkanmaa"
