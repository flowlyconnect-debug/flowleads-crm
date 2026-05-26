import json
from unittest.mock import MagicMock, patch

import pytest

from app.ai.services import AIEnrichmentService, validate_enrichment_response
from app.ai.triggers import apply_enrichment_on_create, has_enrichment_fields
from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService, get_default_stage
from app.users.services import create_organization, create_user

VALID_AI_RESPONSE = {
    "summary": "Strong B2B prospect for outbound sales.",
    "company_info": {
        "industry": "SaaS",
        "company_size_estimate": "50-200",
        "business_model": "b2b",
        "likely_pain_points": ["scaling sales", "lead quality"],
        "tech_stack_hints": ["Salesforce"],
    },
    "contact_info": {
        "seniority_level": "director",
        "likely_decision_maker": True,
        "best_outreach_angle": "Revenue growth",
    },
    "lead_score": 82,
    "score_reason": "Good fit and seniority.",
}


def _setup_org(app, slug="ai-org"):
    with app.app_context():
        org = create_organization(f"AI Org {slug}", slug)
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
        return {"org_id": org.id, "other_org_id": other.id, "admin_id": admin.id, "stage_id": stage.id}


def _enable_ai(app):
    app.config["AI_ENRICHMENT_ENABLED"] = True
    app.config["AI_AUTO_ENRICH_ON_CREATE"] = True
    app.config["OPENAI_API_KEY"] = "test-openai-key"
    app.config["AI_ENRICHMENT_MODEL"] = "gpt-4o-mini"


def _create_api_key(app, org_id):
    from app.api.services import create_api_key

    with app.app_context():
        _, full_key = create_api_key(org_id, "ai test", test_mode=True)
        db.session.commit()
        return full_key


def _auth_headers(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


# --- Triggering ---


def test_enrichment_queued_on_api_create_when_enabled(client, app):
    ctx = _setup_org(app, "queue-on")
    _enable_ai(app)
    key = _create_api_key(app, ctx["org_id"])

    with patch("app.ai.triggers.get_enrichment_queue") as mock_queue:
        mock_queue.return_value.enqueue = MagicMock()
        response = client.post(
            "/api/v1/leads",
            data=json.dumps(
                {"email": "ai@example.com", "company": "Acme Corp"}
            ),
            headers=_auth_headers(key),
        )
        assert response.status_code == 201
        mock_queue.return_value.enqueue.assert_called_once()

    with app.app_context():
        lead = Lead.query.filter_by(email="ai@example.com").first()
        assert lead.ai_enrichment_status == "pending"


def test_enrichment_not_queued_when_disabled(client, app):
    ctx = _setup_org(app, "queue-off")
    app.config["AI_ENRICHMENT_ENABLED"] = False
    key = _create_api_key(app, ctx["org_id"])

    with patch("app.ai.triggers.get_enrichment_queue") as mock_queue:
        mock_queue.return_value.enqueue = MagicMock()
        response = client.post(
            "/api/v1/leads",
            data=json.dumps(
                {"email": "off@example.com", "company": "Acme Corp"}
            ),
            headers=_auth_headers(key),
        )
        assert response.status_code == 201
        mock_queue.return_value.enqueue.assert_not_called()

    with app.app_context():
        lead = Lead.query.filter_by(email="off@example.com").first()
        assert lead.ai_enrichment_status == "disabled"


def test_enrichment_skipped_without_company_website_linkedin(client, app):
    ctx = _setup_org(app, "skip-fields")
    _enable_ai(app)
    key = _create_api_key(app, ctx["org_id"])

    with patch("app.ai.triggers.get_enrichment_queue") as mock_queue:
        mock_queue.return_value.enqueue = MagicMock()
        response = client.post(
            "/api/v1/leads",
            data=json.dumps({"email": "minimal@example.com", "first_name": "Min"}),
            headers=_auth_headers(key),
        )
        assert response.status_code == 201
        mock_queue.return_value.enqueue.assert_not_called()

    with app.app_context():
        lead = Lead.query.filter_by(email="minimal@example.com").first()
        assert lead.ai_enrichment_status == "disabled"


def test_failed_queue_does_not_crash_lead_creation(client, app):
    ctx = _setup_org(app, "queue-fail")
    _enable_ai(app)
    key = _create_api_key(app, ctx["org_id"])

    with patch("app.ai.triggers.get_enrichment_queue") as mock_queue:
        mock_queue.return_value.enqueue.side_effect = RuntimeError("queue down")
        response = client.post(
            "/api/v1/leads",
            data=json.dumps(
                {"email": "safe@example.com", "company": "Safe Inc"}
            ),
            headers=_auth_headers(key),
        )
        assert response.status_code == 201
        assert response.get_json()["success"] is True


# --- Saving result ---


@patch("app.ai.services.call_openai_enrichment")
def test_mocked_openai_updates_lead_and_activity(mock_openai, app):
    ctx = _setup_org(app, "save")
    _enable_ai(app)
    mock_openai.return_value = (
        VALID_AI_RESPONSE,
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )

    with app.app_context():
        lead = LeadService.create(
            {"email": "save@example.com", "company": "Save Co"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        lead.ai_enrichment_status = "processing"
        db.session.commit()
        lead_id = lead.id

        service = AIEnrichmentService()
        assert service.enrich_lead(lead_id) is True

        lead = db.session.get(Lead, lead_id)
        assert lead.ai_enriched is True
        assert lead.ai_enriched_at is not None
        assert lead.ai_summary == VALID_AI_RESPONSE["summary"]
        assert lead.ai_company_info["industry"] == "SaaS"
        assert lead.ai_contact_info["seniority_level"] == "director"
        assert lead.score == 82
        assert lead.score_reason == VALID_AI_RESPONSE["score_reason"]
        assert lead.ai_enrichment_status == "completed"
        assert lead.ai_enrichment_error is None

        activity = Activity.query.filter_by(lead_id=lead_id, type="ai_enriched").first()
        assert activity is not None
        assert "Score: 82" in activity.content
        assert activity.metadata_json["model"] == "gpt-4o-mini"
        assert activity.metadata_json["total_tokens"] == 150


# --- Failure ---


@patch("app.ai.services.call_openai_enrichment")
def test_invalid_json_retries_then_fails(mock_openai, app):
    ctx = _setup_org(app, "json-fail")
    _enable_ai(app)

    mock_openai.side_effect = [
        ({"summary": "x"}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
        ({"summary": "x"}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
        ({"summary": "x"}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
    ]

    with app.app_context():
        lead = LeadService.create(
            {"email": "json@example.com", "company": "Json Co"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        lead.ai_enrichment_status = "processing"
        db.session.commit()

        service = AIEnrichmentService()
        assert service.enrich_lead(lead.id) is False
        assert mock_openai.call_count == 3

        lead = db.session.get(Lead, lead.id)
        assert lead.ai_enrichment_status == "failed"
        assert lead.ai_enrichment_error
        assert "sk-" not in (lead.ai_enrichment_error or "")


@patch("app.ai.triggers.get_enrichment_queue")
def test_lead_creation_succeeds_when_enrichment_fails(mock_queue, client, app):
    ctx = _setup_org(app, "create-ok")
    _enable_ai(app)
    key = _create_api_key(app, ctx["org_id"])
    mock_queue.return_value.enqueue = MagicMock()

    with patch("app.ai.services.call_openai_enrichment", side_effect=RuntimeError("boom")):
        with app.app_context():
            lead = Lead.query.filter_by(email="boom@example.com").first()
            assert lead is None

        response = client.post(
            "/api/v1/leads",
            data=json.dumps(
                {"email": "boom@example.com", "company": "Boom LLC"}
            ),
            headers=_auth_headers(key),
        )
        assert response.status_code == 201


# --- Manual enrichment ---


def test_ui_route_queues_enrichment(client, app):
    ctx = _setup_org(app, "ui-enrich")
    _enable_ai(app)
    _login(client, f"admin-ui-enrich@test.com")

    with app.app_context():
        lead = LeadService.create(
            {"email": "ui@example.com", "company": "UI Co"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        lead.ai_enrichment_status = "disabled"
        db.session.commit()
        lead_id = lead.id

    with patch("app.ai.triggers.get_enrichment_queue") as mock_queue:
        mock_queue.return_value.enqueue = MagicMock()
        response = client.post(f"/leads/{lead_id}/enrich", follow_redirects=False)
        assert response.status_code == 302
        mock_queue.return_value.enqueue.assert_called_once()


def test_api_route_queues_enrichment(client, app):
    ctx = _setup_org(app, "api-enrich")
    _enable_ai(app)
    key = _create_api_key(app, ctx["org_id"])

    with app.app_context():
        lead = LeadService.create(
            {"email": "api-en@example.com", "company": "API Co"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        lead_id = lead.id

    with patch("app.ai.triggers.get_enrichment_queue") as mock_queue:
        mock_queue.return_value.enqueue = MagicMock()
        response = client.post(
            f"/api/v1/leads/{lead_id}/enrich",
            headers=_auth_headers(key),
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["data"]["status"] == "pending"


def test_cross_tenant_api_enrich_returns_404(client, app):
    ctx = _setup_org(app, "cross")
    _enable_ai(app)
    key = _create_api_key(app, ctx["org_id"])

    with app.app_context():
        other_lead = LeadService.create(
            {"email": "other@example.com", "company": "Other"},
            ctx["other_org_id"],
            None,
        )
        db.session.commit()
        other_id = other_lead.id

    response = client.post(
        f"/api/v1/leads/{other_id}/enrich",
        headers=_auth_headers(key),
    )
    assert response.status_code == 404


def test_disabled_ai_returns_ai_disabled(client, app):
    ctx = _setup_org(app, "disabled-api")
    app.config["AI_ENRICHMENT_ENABLED"] = False
    key = _create_api_key(app, ctx["org_id"])

    with app.app_context():
        lead = LeadService.create(
            {"email": "dis@example.com", "company": "Dis Co"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        lead_id = lead.id

    response = client.post(
        f"/api/v1/leads/{lead_id}/enrich",
        headers=_auth_headers(key),
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data["error"]["code"] == "ai_disabled"


# --- Validation ---


def test_validate_enrichment_response_normalizes():
    result = validate_enrichment_response(VALID_AI_RESPONSE)
    assert result["lead_score"] == 82
    assert result["company_info"]["business_model"] == "b2b"


def test_validate_rejects_invalid_score():
    bad = {**VALID_AI_RESPONSE, "lead_score": 150}
    with pytest.raises(ValueError):
        validate_enrichment_response(bad)


# --- UI rendering ---


@patch("app.ai.services.call_openai_enrichment")
def test_detail_shows_score_badge_and_summary(mock_openai, client, app):
    ctx = _setup_org(app, "ui-render")
    _enable_ai(app)
    mock_openai.return_value = (
        VALID_AI_RESPONSE,
        {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    )
    _login(client, f"admin-ui-render@test.com")

    with app.app_context():
        lead = LeadService.create(
            {"email": "render@example.com", "company": "Render Inc"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        lead.ai_enrichment_status = "processing"
        db.session.commit()
        lead_id = lead.id
        AIEnrichmentService().enrich_lead(lead_id)

    response = client.get(f"/leads/{lead_id}")
    html = response.data.decode()
    assert "score-high" in html
    assert "Strong B2B prospect" in html
    assert "AI Enriched" in html


def test_detail_processing_state(client, app):
    ctx = _setup_org(app, "processing-ui")
    _enable_ai(app)
    _login(client, f"admin-processing-ui@test.com")

    with app.app_context():
        lead = LeadService.create(
            {"email": "proc@example.com", "company": "Proc LLC"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        lead.ai_enrichment_status = "processing"
        db.session.commit()
        lead_id = lead.id

    response = client.get(f"/leads/{lead_id}")
    html = response.data.decode()
    assert "Enriching" in html
    assert "ai-pending" in html


def test_score_badge_colors_in_list(client, app):
    ctx = _setup_org(app, "badges")
    _login(client, f"admin-badges@test.com")

    with app.app_context():
        low = LeadService.create(
            {"email": "low@example.com", "company": "L", "score": 30},
            ctx["org_id"],
            ctx["admin_id"],
        )
        mid = LeadService.create(
            {"email": "mid@example.com", "company": "M", "score": 55},
            ctx["org_id"],
            ctx["admin_id"],
        )
        high = LeadService.create(
            {"email": "high@example.com", "company": "H", "score": 90},
            ctx["org_id"],
            ctx["admin_id"],
        )
        none = LeadService.create(
            {"email": "none@example.com", "company": "N"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()

    response = client.get("/leads/")
    html = response.data.decode()
    assert "score-low" in html
    assert "score-mid" in html
    assert "score-high" in html
    assert "score-none" in html or "No score" in html


def test_has_enrichment_fields():
    lead = Lead(company="Acme")
    assert has_enrichment_fields(lead) is True
    lead2 = Lead(email="x@y.com")
    assert has_enrichment_fields(lead2) is False


def test_apply_enrichment_on_create_disabled_when_no_fields(app):
    ctx = _setup_org(app, "no-fields")
    _enable_ai(app)
    with app.app_context():
        lead = Lead(
            organization_id=ctx["org_id"],
            email="bare@example.com",
            stage_id=ctx["stage_id"],
            source="manual",
        )
        db.session.add(lead)
        db.session.flush()
        apply_enrichment_on_create(lead)
        assert lead.ai_enrichment_status == "disabled"
