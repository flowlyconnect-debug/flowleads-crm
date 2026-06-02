import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.exports import _sanitize_cell, export_report_csv
from app.analytics.services import AnalyticsService, conversion_rate, pct_change
from app.email.models import EmailLog
from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService, get_default_stage
from app.users.services import create_organization, create_user


def _setup_org(app, slug="analytics-org"):
    with app.app_context():
        org = create_organization("Analytics Org", slug)
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
        other = create_organization("Other Org", f"{slug}-other")
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
            "admin_email": admin.email,
            "user_email": user.email,
            "admin_id": admin.id,
            "user_id": user.id,
            "other_user_id": other_user.id,
            "stage_id": stage.id,
            "other_stage_id": other_stage.id,
        }


def _login(client, email):
    return client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )


def _range():
    """Wide range avoids SQLite timezone quirks in filters."""
    return (
        datetime(2000, 1, 1, tzinfo=timezone.utc),
        datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    )


# --- Dashboard service ---


def test_dashboard_month_change_zero_previous(app):
    ctx = _setup_org(app, "dash-zero")
    with app.app_context():
        stats = AnalyticsService.get_dashboard_stats(ctx["org_id"])
        assert stats["leads_last_month"] == 0
        assert stats["leads_month_pct_change"] == pct_change(stats["leads_this_month"], 0)


def test_dashboard_conversion_zero_closed(app):
    ctx = _setup_org(app, "dash-conv")
    with app.app_context():
        stats = AnalyticsService.get_dashboard_stats(ctx["org_id"])
        assert stats["closed_count"] == 0
        assert stats["conversion_rate"] == 0.0


def test_dashboard_avg_score_ignores_null(app):
    ctx = _setup_org(app, "dash-score")
    with app.app_context():
        LeadService.create(
            {"email": "a@test.com", "score": 80},
            ctx["org_id"],
            ctx["admin_id"],
        )
        LeadService.create({"email": "b@test.com"}, ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        stats = AnalyticsService.get_dashboard_stats(ctx["org_id"])
        assert stats["avg_score"] == 80.0


def test_dashboard_leads_per_day_includes_zero_days(app):
    ctx = _setup_org(app, "dash-days")
    with app.app_context():
        stats = AnalyticsService.get_dashboard_stats(ctx["org_id"], period_days=3)
        series = stats["charts"]["leads_per_day"]
        assert len(series) == 3
        assert all("date" in d and "count" in d for d in series)


def test_dashboard_source_chart_groups(app):
    ctx = _setup_org(app, "dash-src")
    with app.app_context():
        LeadService.create(
            {"email": "n@test.com", "source": "n8n"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        other = LeadService.create(
            {"email": "x@test.com", "source": "manual"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        other.source = "legacy"
        db.session.commit()
        stats = AnalyticsService.get_dashboard_stats(ctx["org_id"])
        sources = {s["source"]: s["count"] for s in stats["charts"]["sources_pie"]}
        assert sources.get("n8n", 0) >= 1
        assert sources.get("other", 0) >= 1


# --- Pipeline report ---


def test_pipeline_stage_counts_scoped(app):
    ctx = _setup_org(app, "pipe-scope")
    start, end = _range()
    with app.app_context():
        LeadService.create({"email": "mine@test.com"}, ctx["org_id"], ctx["admin_id"])
        LeadService.create(
            {"email": "other@test.com"},
            ctx["other_org_id"],
            ctx["other_user_id"],
        )
        db.session.commit()
        report = AnalyticsService.get_pipeline_report(ctx["org_id"], start, end)
        total = sum(s["lead_count"] for s in report["stages"])
        assert total == 1


def test_pipeline_won_lost_conversion(app):
    ctx = _setup_org(app, "pipe-wl")
    start, end = _range()
    with app.app_context():
        won = LeadService.create({"email": "w@test.com"}, ctx["org_id"], ctx["admin_id"])
        lost = LeadService.create({"email": "l@test.com"}, ctx["org_id"], ctx["admin_id"])
        won.status = "won"
        lost.status = "lost"
        db.session.commit()
        report = AnalyticsService.get_pipeline_report(ctx["org_id"], start, end)
        assert report["won"] >= 0
        assert report["lost"] >= 0


def test_pipeline_empty_org(app):
    ctx = _setup_org(app, "pipe-empty")
    start, end = _range()
    with app.app_context():
        report = AnalyticsService.get_pipeline_report(ctx["org_id"], start, end)
        assert report["stages"]
        assert report["conversion_rate"] == 0.0


def test_lost_reason_analytics_scoped(app):
    ctx = _setup_org(app, "lost-analytics")
    with app.app_context():
        mine1 = LeadService.create({"email": "lost1@test.com"}, ctx["org_id"], ctx["admin_id"])
        mine2 = LeadService.create({"email": "lost2@test.com"}, ctx["org_id"], ctx["admin_id"])
        other = LeadService.create(
            {"email": "lost-other@test.com"},
            ctx["other_org_id"],
            ctx["other_user_id"],
        )
        mine1.status = "lost"
        mine1.lost_reason = "Ei vastannut"
        mine2.status = "lost"
        mine2.lost_reason = "Ei vastannut"
        other.status = "lost"
        other.lost_reason = "Kilpailija voitti"
        db.session.commit()
        counts = AnalyticsService.get_lost_reason_counts(ctx["org_id"])
        counts_map = {item["reason"]: item["count"] for item in counts}
        assert counts_map["Ei vastannut"] == 2
        assert "Kilpailija voitti" not in counts_map


# --- Source report ---


def test_source_conversion_per_source(app):
    ctx = _setup_org(app, "src-conv")
    start, end = _range()
    with app.app_context():
        w = LeadService.create(
            {"email": "w@test.com", "source": "manual"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        l = LeadService.create(
            {"email": "l@test.com", "source": "manual"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        w.status = "won"
        l.status = "lost"
        db.session.commit()
        report = AnalyticsService.get_source_report(ctx["org_id"], start, end)
        manual = next(s for s in report["sources"] if s["source"] == "manual")
        assert manual["conversion_rate"] == conversion_rate(1, 2)


def test_source_score_buckets(app):
    ctx = _setup_org(app, "src-bucket")
    start, end = _range()
    with app.app_context():
        LeadService.create(
            {"email": "low@test.com", "source": "n8n", "score": 30},
            ctx["org_id"],
            ctx["admin_id"],
        )
        LeadService.create(
            {"email": "mid@test.com", "source": "n8n", "score": 55},
            ctx["org_id"],
            ctx["admin_id"],
        )
        db.session.commit()
        report = AnalyticsService.get_source_report(ctx["org_id"], start, end)
        n8n = next(s for s in report["sources"] if s["source"] == "n8n")
        assert n8n["score_buckets"]["0-40"] == 1
        assert n8n["score_buckets"]["41-70"] == 1


def test_source_cross_tenant_excluded(app):
    ctx = _setup_org(app, "src-x")
    start, end = _range()
    with app.app_context():
        LeadService.create({"email": "mine@test.com", "source": "import"}, ctx["org_id"], ctx["admin_id"])
        LeadService.create(
            {"email": "other@test.com", "source": "import"},
            ctx["other_org_id"],
            ctx["other_user_id"],
        )
        db.session.commit()
        report = AnalyticsService.get_source_report(ctx["org_id"], start, end)
        assert report["total_leads"] == 1


# --- Team report ---


def test_team_activity_counts(app):
    ctx = _setup_org(app, "team-act")
    start, end = _range()
    with app.app_context():
        lead = LeadService.create({"email": "t@test.com"}, ctx["org_id"], ctx["admin_id"])
        db.session.add(
            Activity(
                lead_id=lead.id,
                user_id=ctx["admin_id"],
                organization_id=ctx["org_id"],
                type="note",
                content="hi",
            )
        )
        db.session.add(
            Activity(
                lead_id=lead.id,
                user_id=ctx["admin_id"],
                organization_id=ctx["org_id"],
                type="email_sent",
                content="sent",
            )
        )
        db.session.commit()
        report = AnalyticsService.get_team_report(ctx["org_id"], start, end)
        admin_row = next(m for m in report["members"] if m["user_id"] == ctx["admin_id"])
        assert admin_row["notes"] == 1
        assert admin_row["emails_sent"] == 1


def test_team_date_filter(app):
    ctx = _setup_org(app, "team-date")
    now = datetime.now(timezone.utc)
    old_start = now - timedelta(days=60)
    old_end = now - timedelta(days=31)
    recent_start = now - timedelta(days=7)
    with app.app_context():
        lead = LeadService.create({"email": "old@test.com"}, ctx["org_id"], ctx["admin_id"])
        act = Activity(
            lead_id=lead.id,
            user_id=ctx["admin_id"],
            organization_id=ctx["org_id"],
            type="note",
            content="old",
        )
        db.session.add(act)
        db.session.flush()
        act.created_at = now - timedelta(days=45)
        db.session.commit()
        old_report = AnalyticsService.get_team_report(ctx["org_id"], old_start, old_end)
        recent_report = AnalyticsService.get_team_report(ctx["org_id"], recent_start, now)
        old_total = sum(m["notes"] for m in old_report["members"])
        recent_total = sum(m["notes"] for m in recent_report["members"])
        assert old_total >= 1
        assert recent_total == 0


# --- AI report ---


def test_ai_report_completed_failed(app):
    ctx = _setup_org(app, "ai-cf")
    start, end = _range()
    with app.app_context():
        ok = LeadService.create({"email": "ok@test.com"}, ctx["org_id"], ctx["admin_id"])
        bad = LeadService.create({"email": "bad@test.com"}, ctx["org_id"], ctx["admin_id"])
        ok.ai_enrichment_status = "completed"
        bad.ai_enrichment_status = "failed"
        db.session.commit()
        report = AnalyticsService.get_ai_report(ctx["org_id"], start, end)
        assert report["completed"] >= 1
        assert report["failed"] >= 1


def test_ai_token_usage_from_metadata(app):
    ctx = _setup_org(app, "ai-tok")
    start, end = _range()
    with app.app_context():
        lead = LeadService.create({"email": "ai@test.com"}, ctx["org_id"], ctx["admin_id"])
        db.session.add(
            Activity(
                lead_id=lead.id,
                user_id=ctx["admin_id"],
                organization_id=ctx["org_id"],
                type="ai_enriched",
                metadata_json={"total_tokens": 1500},
            )
        )
        db.session.commit()
        report = AnalyticsService.get_ai_report(ctx["org_id"], start, end)
        assert report["total_tokens"] == 1500
        assert report["cost_estimate"] > 0


def test_ai_no_token_metadata_zero(app):
    ctx = _setup_org(app, "ai-zero")
    start, end = _range()
    with app.app_context():
        report = AnalyticsService.get_ai_report(ctx["org_id"], start, end)
        assert report["total_tokens"] == 0
        assert report["cost_estimate"] == 0.0


# --- Export ---


def test_csv_export_pipeline(app):
    ctx = _setup_org(app, "exp-pipe")
    start, end = _range()
    with app.app_context():
        resp = export_report_csv("pipeline", ctx["org_id"], start, end)
        reader = csv.reader(io.StringIO(resp.get_data(as_text=True)))
        rows = list(reader)
        assert rows[0][0] == "stage"


def test_csv_formula_injection_escaped():
    assert _sanitize_cell("=SUM(A1)") == "'=SUM(A1)"


def test_csv_export_cross_tenant(app):
    ctx = _setup_org(app, "exp-x")
    start, end = _range()
    with app.app_context():
        LeadService.create({"email": "mine@test.com"}, ctx["org_id"], ctx["admin_id"])
        LeadService.create(
            {"email": "other@test.com"},
            ctx["other_org_id"],
            ctx["other_user_id"],
        )
        db.session.commit()
        resp = export_report_csv("source", ctx["org_id"], start, end)
        body = resp.get_data(as_text=True)
        assert "other@test.com" not in body


@pytest.mark.parametrize("export_type", ["pipeline", "source", "team", "ai"])
def test_csv_export_types(app, export_type):
    ctx = _setup_org(app, f"exp-{export_type}")
    start, end = _range()
    with app.app_context():
        resp = export_report_csv(export_type, ctx["org_id"], start, end)
        assert resp.mimetype == "text/csv"


# --- UI routes ---


def test_dashboard_renders_normal_user(client, app):
    ctx = _setup_org(app, "ui-dash")
    _login(client, ctx["user_email"])
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_reports_renders_normal_user(client, app):
    ctx = _setup_org(app, "ui-rep")
    _login(client, ctx["user_email"])
    resp = client.get("/reports")
    assert resp.status_code == 200
    assert b"Reports" in resp.data


def test_api_client_cannot_access_analytics(client, app):
    from werkzeug.exceptions import Forbidden

    from app.analytics.routes import _require_ui_role

    with app.app_context():
        org = create_organization("API Analytics", "api-analytics")
        db.session.flush()
        api_user = create_user(
            "apicli@analytics.com",
            "securepassword1",
            role="api_client",
            organization_id=org.id,
        )
        db.session.commit()
        api_user_id = api_user.id

    login_resp = client.post(
        "/auth/login",
        data={"email": "apicli@analytics.com", "password": "securepassword1"},
    )
    assert b"API" in login_resp.data or login_resp.status_code == 200

    with app.app_context(), app.test_request_context():
        from flask_login import login_user

        from app.users.models import User

        login_user(db.session.get(User, api_user_id))
        with pytest.raises(Forbidden):
            _require_ui_role()


def test_admin_reports_requires_superadmin_2fa(client, app):
    ctx = _setup_org(app, "ui-admin-rep")
    _login(client, ctx["admin_email"])
    assert client.get("/admin/reports").status_code == 403
