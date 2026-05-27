from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.admin.services import get_accessible_organizations
from app.analytics.date_ranges import resolve_report_dates
from app.analytics.exports import export_report_csv
from app.analytics.services import AnalyticsService
from app.core.permissions import require_2fa, require_role
from app.extensions import cache
from app.leads.permissions import resolve_organization_id

analytics_bp = Blueprint("analytics", __name__)

UI_ROLES = ("superadmin", "admin", "user")
EXPORT_TYPES = frozenset({"pipeline", "source", "team", "ai"})
REPORT_TYPES = frozenset({"pipeline", "source", "team", "ai"})


def _require_ui_role():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in UI_ROLES:
        abort(403)


def _optional_organization_id():
    if current_user.role == "superadmin":
        org_id = request.args.get("organization_id") or request.form.get("organization_id")
        if not org_id:
            return None
        try:
            return int(org_id)
        except (TypeError, ValueError):
            abort(400, description="Invalid organization_id.")
    if current_user.organization_id is None:
        abort(403)
    return current_user.organization_id


def _get_cached_dashboard_stats(organization_id: int, period_days: int = 30) -> dict:
    from flask import current_app

    timeout = int(current_app.config.get("DASHBOARD_CACHE_SECONDS", 300))
    cache_key = f"dashboard_stats:{organization_id}:{period_days}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    data = AnalyticsService.get_dashboard_stats(organization_id, period_days=period_days)
    cache.set(cache_key, data, timeout=timeout)
    return data


@analytics_bp.before_request
@login_required
def block_api_client():
    _require_ui_role()


@analytics_bp.route("/dashboard")
@require_role(*UI_ROLES)
@require_2fa
def dashboard():
    from flask import current_app

    organization_id = _optional_organization_id()
    organizations = get_accessible_organizations() if current_user.is_superadmin() else []

    try:
        current_app.logger.info(
            "dashboard_debug: user_id=%s role=%s org_id=%s org_count=%s",
            getattr(current_user, "id", None),
            getattr(current_user, "role", None),
            getattr(current_user, "organization_id", None),
            len(organizations),
        )
    except Exception:
        # Best-effort debug logging; ignore all errors.
        pass

    if organization_id is None:
        return render_template(
            "analytics/dashboard.html",
            org_picker=True,
            organizations=organizations,
            organization_id=None,
        )

    period = request.args.get("period", 30, type=int)
    if period not in (1, 7, 30):
        period = 30

    stats = _get_cached_dashboard_stats(organization_id, period_days=period)
    extended = AnalyticsService.get_dashboard_extended_metrics(organization_id)
    won_deals_chart = AnalyticsService.get_won_deals_monthly_chart(organization_id)
    sales_projection_chart = AnalyticsService.get_sales_projection_chart(organization_id)
    pipeline_stages = AnalyticsService.get_pipeline_stages_donut(organization_id)
    loss_reasons = AnalyticsService.get_loss_reasons_donut(organization_id)

    from app.users.models import Organization, User
    from app.leads.models import PipelineStage

    org = Organization.query.get(organization_id)
    org_users = User.query.filter_by(organization_id=organization_id, is_active=True).order_by(
        User.email
    ).all()
    stages = (
        PipelineStage.query.filter_by(organization_id=organization_id)
        .order_by(PipelineStage.order_index)
        .all()
    )

    now = datetime.now(timezone.utc)
    end_date = now.date().isoformat()
    start_date = (now - timedelta(days=period - 1)).date().isoformat()

    activity_feed = AnalyticsService.get_recent_activity(organization_id)
    from app.tasks.services import TaskService

    tasks_today = TaskService.get_due_today(current_user.id, organization_id)
    tasks_overdue = TaskService.get_overdue(organization_id, user_id=current_user.id)
    recent_tasks = TaskService.get_recent(organization_id, current_user.id, limit=5)
    from app.calendar.services import CalendarService

    upcoming_meetings = CalendarService.get_upcoming_meetings(
        current_user.id, organization_id, limit=3
    )
    from app.proposals.services import ProposalService

    open_proposals_count = ProposalService.get_open_count(organization_id)
    accepted_proposals_month = ProposalService.get_accepted_this_month_total(organization_id)
    from app.forms.services import WebFormService

    form_submissions_today = WebFormService.submissions_today_count(organization_id)
    from app.analytics.currency import currency_symbol, get_default_currency
    from app.analytics.prediction import PredictionService

    forecast = PredictionService.calculate_forecast(organization_id, period_days=30)
    high_potential = PredictionService.get_high_potential_leads(organization_id, limit=5)
    org_currency = get_default_currency(organization_id)
    return render_template(
        "analytics/dashboard.html",
        org_picker=False,
        organizations=organizations,
        organization_id=organization_id,
        org=org,
        period=period,
        start_date=start_date,
        end_date=end_date,
        org_users=org_users,
        stages=stages,
        stats=stats,
        extended=extended,
        won_deals_chart=won_deals_chart,
        sales_projection_chart=sales_projection_chart,
        pipeline_stages=pipeline_stages,
        loss_reasons=loss_reasons,
        activity_feed=activity_feed,
        tasks_today_count=len(tasks_today),
        tasks_overdue_count=len(tasks_overdue),
        recent_tasks=recent_tasks,
        upcoming_meetings=upcoming_meetings,
        open_proposals_count=open_proposals_count,
        accepted_proposals_month=accepted_proposals_month,
        form_submissions_today=form_submissions_today,
        sales_forecast=forecast,
        high_potential_leads=high_potential,
        org_currency=org_currency,
        currency_symbol=currency_symbol(org_currency),
    )


@analytics_bp.route("/reports")
@require_role(*UI_ROLES)
@require_2fa
def reports():
    organization_id = _optional_organization_id()
    organizations = get_accessible_organizations() if current_user.is_superadmin() else []

    report_type = request.args.get("report", "pipeline")
    if report_type not in REPORT_TYPES:
        report_type = "pipeline"

    range_key = request.args.get("range", "this_month")
    start_param = request.args.get("start")
    end_param = request.args.get("end")
    start_dt, end_dt, range_error = resolve_report_dates(range_key, start=start_param, end=end_param)

    report_data = None
    if organization_id is not None and not range_error:
        loaders = {
            "pipeline": AnalyticsService.get_pipeline_report,
            "source": AnalyticsService.get_source_report,
            "team": AnalyticsService.get_team_report,
            "ai": AnalyticsService.get_ai_report,
        }
        report_data = loaders[report_type](organization_id, start_dt, end_dt)

    return render_template(
        "analytics/reports.html",
        organizations=organizations,
        organization_id=organization_id,
        report_type=report_type,
        range_key=range_key,
        start_date=start_param or start_dt.date().isoformat(),
        end_date=end_param or end_dt.date().isoformat(),
        range_error=range_error,
        report_data=report_data,
    )


@analytics_bp.route("/reports/forecast")
@require_role(*UI_ROLES)
@require_2fa
def forecast():
    organization_id = _optional_organization_id()
    organizations = get_accessible_organizations() if current_user.is_superadmin() else []

    period_days = request.args.get("period", 30, type=int)
    if period_days not in (30, 60, 90):
        period_days = 30

    forecast_data = None
    deals = []
    org_currency = "EUR"
    currency_sym = "€"
    if organization_id is not None:
        from app.analytics.currency import currency_symbol, get_default_currency
        from app.analytics.prediction import PredictionService

        org_currency = get_default_currency(organization_id)
        currency_sym = currency_symbol(org_currency)
        forecast_data = PredictionService.calculate_forecast(organization_id, period_days=period_days)
        deals = PredictionService.get_forecast_deals(organization_id)

    return render_template(
        "analytics/forecast.html",
        organizations=organizations,
        organization_id=organization_id,
        forecast=forecast_data,
        deals=deals,
        period_days=period_days,
        org_currency=org_currency,
        currency_symbol=currency_sym,
    )


@analytics_bp.route("/reports/export")
@require_role(*UI_ROLES)
def reports_export():
    organization_id = resolve_organization_id()
    export_type = request.args.get("type", "")
    if export_type not in EXPORT_TYPES:
        flash("Invalid export type.", "danger")
        return redirect(url_for("analytics.reports", organization_id=organization_id))

    range_key = request.args.get("range", "this_month")
    start_dt, end_dt, range_error = resolve_report_dates(
        range_key,
        start=request.args.get("start"),
        end=request.args.get("end"),
    )
    if range_error:
        flash(range_error, "danger")
        return redirect(url_for("analytics.reports", organization_id=organization_id))

    return export_report_csv(export_type, organization_id, start_dt, end_dt)


@analytics_bp.route("/admin/reports")
@login_required
@require_role("superadmin")
@require_2fa
def admin_reports():
    range_key = request.args.get("range", "this_month")
    start_dt, end_dt, range_error = resolve_report_dates(
        range_key,
        start=request.args.get("start"),
        end=request.args.get("end"),
    )
    stats = None if range_error else AnalyticsService.get_system_report(start_dt, end_dt)
    return render_template(
        "admin/reports.html",
        stats=stats,
        range_key=range_key,
        range_error=range_error,
        start_date=request.args.get("start") or start_dt.date().isoformat(),
        end_date=request.args.get("end") or end_dt.date().isoformat(),
    )
