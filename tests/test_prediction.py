import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.analytics.models import PredictionLog
from app.analytics.prediction import (
    FALLBACK_MODEL_VERSION,
    OPENAI_PREDICTION_MODEL_VERSION,
    PredictionService,
    PredictionServiceError,
    run_weekly_batch_predictions,
)
from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService, get_default_stage
from app.users.services import create_organization, create_user


def _setup_org(app, slug="pred-org"):
    with app.app_context():
        org = create_organization(f"Pred Org {slug}", slug)
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
            f"other-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=other.id,
        )
        db.session.commit()
        stage = get_default_stage(org.id)
        other_stage = get_default_stage(other.id)
        return {
            "org_id": org.id,
            "other_org_id": other.id,
            "admin_id": admin.id,
            "stage_id": stage.id,
            "other_stage_id": other_stage.id,
            "admin_email": admin.email,
        }


def _login(client, email):
    r = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert r.status_code == 302


def _create_lead(app, org_id, stage_id, **kwargs):
    with app.app_context():
        data = {
            "email": kwargs.get("email", f"lead-{org_id}@test.com"),
            "company": kwargs.get("company", "Acme"),
            "stage_id": stage_id,
        }
        if "deal_value" in kwargs:
            data["deal_value"] = kwargs["deal_value"]
        lead = LeadService.create(data, org_id, kwargs.get("user_id"))
        db.session.commit()
        return lead.id


# --- Service: signals ---


def test_signals_collected_from_lead_data(app):
    ctx = _setup_org(app, "signals")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], deal_value=5000)
    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        lead.score = 75
        lead.tags = ["enterprise", "saas"]
        lead.ai_company_info = {"company_size_estimate": "50-200", "industry": "SaaS"}
        db.session.commit()
        LeadService.log_activity(lead_id, ctx["admin_id"], "note", content="Hello")
        LeadService.log_activity(lead_id, ctx["admin_id"], "call", content="Called")
        db.session.commit()

        signals = PredictionService.collect_signals(lead_id)
        assert signals["lead_score"] == 75
        assert signals["activity_counts"]["notes"] >= 1
        assert signals["activity_counts"]["calls"] >= 1
        assert signals["company_size_estimate"] == "50-200"
        assert signals["deal_value"] == 5000.0
        assert "pipeline_stage" in signals


# --- Prediction storage ---


@patch("app.analytics.prediction.call_openai_prediction")
def test_prediction_stores_probability(mock_openai, app):
    ctx = _setup_org(app, "store-prob")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    mock_openai.return_value = {
        "probability": 0.73,
        "key_positive_signals": ["meeting"],
        "key_risk_signals": [],
        "recommendation": "Follow up",
    }
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        prob = PredictionService.predict_lead(lead_id)
        db.session.commit()
        lead = db.session.get(Lead, lead_id)
        assert prob == pytest.approx(0.73)
        assert float(lead.close_probability) == pytest.approx(0.73)


@patch("app.analytics.prediction.call_openai_prediction")
def test_prediction_updates_probability_updated_at(mock_openai, app):
    ctx = _setup_org(app, "updated-at")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    mock_openai.return_value = {"probability": 0.5, "key_positive_signals": [], "key_risk_signals": []}
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        PredictionService.predict_lead(lead_id)
        db.session.commit()
        lead = db.session.get(Lead, lead_id)
        assert lead.probability_updated_at is not None


@patch("app.analytics.prediction.call_openai_prediction")
def test_expected_value_calculated_correctly(mock_openai, app):
    ctx = _setup_org(app, "expected")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], deal_value=10000)
    mock_openai.return_value = {"probability": 0.25, "key_positive_signals": [], "key_risk_signals": []}
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        PredictionService.predict_lead(lead_id)
        db.session.commit()
        lead = db.session.get(Lead, lead_id)
        assert float(lead.expected_value) == pytest.approx(2500.0)


@patch("app.analytics.prediction.call_openai_prediction")
def test_prediction_log_created(mock_openai, app):
    ctx = _setup_org(app, "log-created")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    mock_openai.return_value = {
        "probability": 0.6,
        "key_positive_signals": ["x"],
        "key_risk_signals": ["y"],
        "recommendation": "Do it",
    }
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        PredictionService.predict_lead(lead_id)
        db.session.commit()
        log = PredictionLog.query.filter_by(lead_id=lead_id).first()
        assert log is not None
        assert log.model_version == OPENAI_PREDICTION_MODEL_VERSION
        assert log.recommendation == "Do it"
        assert log.signals is not None


@patch("app.analytics.prediction.call_openai_prediction")
def test_openai_response_parsed_correctly(mock_openai, app):
    ctx = _setup_org(app, "parse")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    mock_openai.return_value = {
        "probability": 0.82,
        "key_positive_signals": ["proposal viewed"],
        "key_risk_signals": [],
        "recommendation": "Close soon",
    }
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        prob = PredictionService.predict_lead(lead_id)
        db.session.commit()
        assert prob == pytest.approx(0.82)
        log = PredictionLog.query.filter_by(lead_id=lead_id).first()
        assert "proposal viewed" in log.key_positive_signals


@patch("app.analytics.prediction.call_openai_prediction")
def test_invalid_openai_response_falls_back(mock_openai, app):
    ctx = _setup_org(app, "invalid-json")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    mock_openai.return_value = {"not_probability": True}
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        prob = PredictionService.predict_lead(lead_id)
        db.session.commit()
        assert 0.0 <= prob <= 1.0
        log = PredictionLog.query.filter_by(lead_id=lead_id).first()
        assert log.model_version == FALLBACK_MODEL_VERSION


@patch("app.analytics.prediction.call_openai_prediction")
def test_probability_clamped_between_0_and_1(mock_openai, app):
    ctx = _setup_org(app, "clamp")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    mock_openai.return_value = {"probability": 1.5, "key_positive_signals": [], "key_risk_signals": []}
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        prob = PredictionService.predict_lead(lead_id)
        assert prob == 1.0


def test_openai_unavailable_uses_fallback(app):
    ctx = _setup_org(app, "no-openai")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    app.config["OPENAI_API_KEY"] = None

    with app.app_context():
        prob = PredictionService.predict_lead(lead_id)
        db.session.commit()
        assert 0.0 <= prob <= 1.0
        log = PredictionLog.query.filter_by(lead_id=lead_id).first()
        assert log.model_version == FALLBACK_MODEL_VERSION


# --- Forecast ---


@patch("app.analytics.prediction.call_openai_prediction")
def test_forecast_weighted_sum(mock_openai, app):
    ctx = _setup_org(app, "forecast-sum")
    l1 = _create_lead(app, ctx["org_id"], ctx["stage_id"], deal_value=10000, email="a@test.com")
    l2 = _create_lead(app, ctx["org_id"], ctx["stage_id"], deal_value=5000, email="b@test.com")
    mock_openai.side_effect = [
        {"probability": 0.5, "key_positive_signals": [], "key_risk_signals": []},
        {"probability": 0.2, "key_positive_signals": [], "key_risk_signals": []},
    ]
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        PredictionService.predict_lead(l1)
        PredictionService.predict_lead(l2)
        db.session.commit()
        forecast = PredictionService.calculate_forecast(ctx["org_id"])
        assert forecast["expected_revenue"] == pytest.approx(6000.0)


@patch("app.analytics.prediction.call_openai_prediction")
def test_forecast_best_case(mock_openai, app):
    ctx = _setup_org(app, "best-case")
    l1 = _create_lead(app, ctx["org_id"], ctx["stage_id"], deal_value=8000, email="c@test.com")
    l2 = _create_lead(app, ctx["org_id"], ctx["stage_id"], deal_value=2000, email="d@test.com")
    mock_openai.side_effect = [
        {"probability": 0.6, "key_positive_signals": [], "key_risk_signals": []},
        {"probability": 0.3, "key_positive_signals": [], "key_risk_signals": []},
    ]
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        for lid in (l1, l2):
            PredictionService.predict_lead(lid)
        db.session.commit()
        forecast = PredictionService.calculate_forecast(ctx["org_id"])
        assert forecast["best_case"] == pytest.approx(8000.0)


@patch("app.analytics.prediction.call_openai_prediction")
def test_forecast_conservative_case(mock_openai, app):
    ctx = _setup_org(app, "conservative")
    l1 = _create_lead(app, ctx["org_id"], ctx["stage_id"], deal_value=10000, email="e@test.com")
    l2 = _create_lead(app, ctx["org_id"], ctx["stage_id"], deal_value=5000, email="f@test.com")
    mock_openai.side_effect = [
        {"probability": 0.9, "key_positive_signals": [], "key_risk_signals": []},
        {"probability": 0.5, "key_positive_signals": [], "key_risk_signals": []},
    ]
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        for lid in (l1, l2):
            PredictionService.predict_lead(lid)
        db.session.commit()
        forecast = PredictionService.calculate_forecast(ctx["org_id"])
        assert forecast["conservative_case"] == pytest.approx(10000.0)


@patch("app.analytics.prediction.call_openai_prediction")
def test_forecast_grouped_by_stage(mock_openai, app):
    ctx = _setup_org(app, "by-stage")
    with app.app_context():
        stages = LeadService.get_pipeline_data(ctx["org_id"])["stages"]
        stage_ids = [s.id for s in stages[:2]]
    l1 = _create_lead(app, ctx["org_id"], stage_ids[0], deal_value=3000, email="g@test.com")
    mock_openai.return_value = {"probability": 0.4, "key_positive_signals": [], "key_risk_signals": []}
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        PredictionService.predict_lead(l1)
        db.session.commit()
        forecast = PredictionService.calculate_forecast(ctx["org_id"])
        assert len(forecast["by_stage"]) >= 1
        total_expected = sum(s["expected_revenue"] for s in forecast["by_stage"].values())
        assert total_expected == pytest.approx(forecast["expected_revenue"])


# --- Batch ---


@patch("app.analytics.prediction.PredictionService.predict_lead")
def test_batch_prediction_continues_if_one_lead_fails(mock_predict, app):
    ctx = _setup_org(app, "batch-fail")
    l1 = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="h@test.com")
    _create_lead(app, ctx["org_id"], ctx["stage_id"], email="i@test.com")

    def side_effect(lead_id):
        if lead_id == l1:
            raise RuntimeError("boom")
        return 0.5

    mock_predict.side_effect = side_effect

    with app.app_context():
        result = PredictionService.predict_batch(ctx["org_id"])
        assert result["processed"] == 1
        assert result["failed"] == 1
        assert len(result["errors"]) == 1


def test_minimal_lead_data_does_not_crash_prediction(app):
    ctx = _setup_org(app, "minimal")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="minimal@test.com")
    app.config["OPENAI_API_KEY"] = None

    with app.app_context():
        prob = PredictionService.predict_lead(lead_id)
        db.session.commit()
        assert 0.0 <= prob <= 1.0


# --- Deal value form ---


def test_deal_value_field_saved_from_lead_edit(app):
    ctx = _setup_org(app, "deal-edit")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])

    with app.app_context():
        LeadService.update(
            lead_id,
            {"deal_value": 12500.50, "company": "Acme Corp"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        lead = db.session.get(Lead, lead_id)
        assert float(lead.deal_value) == pytest.approx(12500.50)


# --- UI routes ---


@patch("app.analytics.prediction.call_openai_prediction")
def test_dashboard_forecast_card_renders(mock_openai, client, app):
    ctx = _setup_org(app, "dash-forecast")
    _create_lead(app, ctx["org_id"], ctx["stage_id"], deal_value=1000)
    mock_openai.return_value = {"probability": 0.5, "key_positive_signals": [], "key_risk_signals": []}
    app.config["OPENAI_API_KEY"] = "test-key"
    _login(client, ctx["admin_email"])

    with app.app_context():
        leads = Lead.query.filter_by(organization_id=ctx["org_id"]).all()
        for lead in leads:
            PredictionService.predict_lead(lead.id)
        db.session.commit()

    response = client.get(f"/dashboard?organization_id={ctx['org_id']}")
    assert response.status_code == 200
    assert b"Myyntiennuste" in response.data


@patch("app.analytics.prediction.call_openai_prediction")
def test_pipeline_probability_badge_renders(mock_openai, client, app):
    ctx = _setup_org(app, "pipe-badge")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    mock_openai.return_value = {"probability": 0.75, "key_positive_signals": [], "key_risk_signals": []}
    app.config["OPENAI_API_KEY"] = "test-key"

    with app.app_context():
        PredictionService.predict_lead(lead_id)
        db.session.commit()

    _login(client, ctx["admin_email"])
    response = client.get(f"/leads/pipeline?organization_id={ctx['org_id']}")
    assert response.status_code == 200
    assert b"75%" in response.data or b"score-high" in response.data


def test_reports_forecast_renders(client, app):
    ctx = _setup_org(app, "forecast-page")
    _login(client, ctx["admin_email"])
    response = client.get(f"/reports/forecast?organization_id={ctx['org_id']}")
    assert response.status_code == 200
    assert b"Myyntiennuste" in response.data


def test_cross_tenant_isolation_forecast(app):
    ctx = _setup_org(app, "isolation")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], deal_value=9000)

    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        lead.close_probability = Decimal("0.8")
        lead.deal_value = Decimal("9000")
        db.session.commit()

        other_forecast = PredictionService.calculate_forecast(ctx["other_org_id"])
        assert other_forecast["expected_revenue"] == 0.0
        assert other_forecast["leads_count"] == 0

        own = PredictionService.calculate_forecast(ctx["org_id"])
        assert own["leads_count"] >= 1


@patch("app.analytics.prediction.PredictionService.predict_batch")
def test_scheduler_job_calls_batch_prediction_safely(mock_batch, app):
    ctx = _setup_org(app, "sched-job")
    _create_lead(app, ctx["org_id"], ctx["stage_id"])
    mock_batch.return_value = {"processed": 1, "failed": 0, "errors": []}

    run_weekly_batch_predictions(app)
    assert mock_batch.called
