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
from sqlalchemy import func
from sqlalchemy.orm import aliased

from app.admin.services import get_accessible_organizations
from app.analytics.date_ranges import resolve_report_dates
from app.analytics.exports import export_report_csv
from app.analytics.dashboard_today import DashboardTodayService
from app.analytics.services import AnalyticsService
from app.core.errors import json_success
from app.core.permissions import require_2fa, require_role
from app.extensions import cache, db
from app.leads.models import Activity, Lead, PipelineStage
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
    try:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    except Exception as exc:
        current_app.logger.warning("Dashboard cache read failed: %s", exc)

    data = AnalyticsService.get_dashboard_stats(organization_id, period_days=period_days)
    try:
        cache.set(cache_key, data, timeout=timeout)
    except Exception as exc:
        current_app.logger.warning("Dashboard cache write failed: %s", exc)
    return data


def _pipeline_bucket_label(stage_name: str | None, status: str | None) -> str | None:
    """Map stage + status to one of the dashboard pipeline buckets."""
    if not status:
        return None
    if status == "won":
        return "Voitettu"
    if status == "lost":
        return "Hävitty"
    # Non-closed: bucket by stage name
    name = (stage_name or "").strip()
    if not name:
        return None
    if name in {"Uusi liidi", "New Lead"}:
        return "Uusi liidi"
    if name in {"Kontaktoitu", "Contacted"}:
        return "Kontaktoitu"
    if name in {"Kvalifioitu", "Qualified", "Interested"}:
        return "Kvalifioitu"
    if name in {"Tarjous lähetetty", "Proposal Sent", "Proposal"}:
        return "Tarjous lähetetty"
    return None


def _ai_recommendations_for_context(organization_id: int, context: str) -> list[dict]:
    from app.proposals.models import Proposal
    from app.sequences.models import EmailSequenceEnrollment
    from app.tasks.models import Task

    now = datetime.now(timezone.utc)
    recs: list[dict] = []

    overdue_tasks = (
        Task.query.filter(
            Task.organization_id == organization_id,
            Task.status.in_(("pending", "in_progress")),
            Task.due_date < now,
        )
        .order_by(Task.due_date.asc())
        .limit(10)
        .count()
    )
    stale_leads = (
        Lead.query.filter(
            Lead.organization_id == organization_id,
            Lead.status == "active",
            Lead.last_contacted_at.isnot(None),
            Lead.last_contacted_at < now - timedelta(days=14),
        )
        .order_by(Lead.last_contacted_at.asc())
        .limit(10)
        .count()
    )
    no_contact_count = (
        Lead.query.filter(
            Lead.organization_id == organization_id,
            Lead.status == "active",
            Lead.last_contacted_at.is_(None),
        )
        .limit(10)
        .count()
    )
    high_score_push = (
        Lead.query.filter(
            Lead.organization_id == organization_id,
            Lead.status == "active",
            Lead.score.isnot(None),
            Lead.score >= 80,
        )
        .order_by(Lead.score.desc())
        .limit(10)
        .count()
    )
    expiring_proposals = (
        Proposal.query.filter(
            Proposal.organization_id == organization_id,
            Proposal.status.in_(("sent", "viewed")),
            Proposal.valid_until.isnot(None),
            Proposal.valid_until <= (now + timedelta(days=7)).date(),
        )
        .order_by(Proposal.valid_until.asc())
        .limit(10)
        .count()
    )
    proposals_viewed_not_accepted = (
        Proposal.query.filter(
            Proposal.organization_id == organization_id,
            Proposal.status == "viewed",
        )
        .order_by(Proposal.last_opened_at.desc())
        .limit(10)
        .count()
    )
    proposals_unviewed_sent = (
        Proposal.query.filter(
            Proposal.organization_id == organization_id,
            Proposal.status == "sent",
            Proposal.sent_at.isnot(None),
            Proposal.sent_at <= now - timedelta(days=7),
        )
        .order_by(Proposal.sent_at.asc())
        .limit(10)
        .count()
    )

    leads_without_tasks = (
        db.session.query(Lead.id)
        .outerjoin(
            Task,
            (Task.lead_id == Lead.id)
            & (Task.organization_id == organization_id)
            & (Task.status.in_(("pending", "in_progress"))),
        )
        .filter(
            Lead.organization_id == organization_id,
            Lead.status == "active",
            Task.id.is_(None),
        )
        .limit(10)
        .count()
    )
    high_score_no_contact = (
        Lead.query.filter(
            Lead.organization_id == organization_id,
            Lead.status == "active",
            Lead.score.isnot(None),
            Lead.score >= 80,
            Lead.last_contacted_at.is_(None),
        )
        .limit(10)
        .count()
    )
    active_sequences = (
        EmailSequenceEnrollment.query.filter(
            EmailSequenceEnrollment.organization_id == organization_id,
            EmailSequenceEnrollment.status == "active",
        )
        .limit(10)
        .count()
    )

    if context == "pipeline":
        if stale_leads:
            recs.append(
                {
                    "icon": "⚠️",
                    "priority": "urgent",
                    "title": "Liidejä riskissä putkessa",
                    "body": f"{stale_leads} liidiä ilman kontaktia yli 14 päivää.",
                    "action_url": "/leads/pipeline",
                    "action_label": "Avaa putki",
                }
            )
        if high_score_push:
            recs.append(
                {
                    "icon": "🔥",
                    "priority": "hot",
                    "title": "Korkean todennäköisyyden liidit valmiina",
                    "body": f"{high_score_push} liidillä score 80+ — nosta ne seuraavaan vaiheeseen.",
                    "action_url": "/leads/pipeline",
                    "action_label": "Priorisoi kuumat",
                }
            )
        if active_sequences:
            recs.append(
                {
                    "icon": "✉️",
                    "priority": "",
                    "title": "Sekvenssit käyvät taustalla",
                    "body": f"{active_sequences} aktiivista sekvenssiä tukee putken etenemistä.",
                    "action_url": "/sequences",
                    "action_label": "Tarkista sekvenssit",
                }
            )
    elif context == "leads":
        if leads_without_tasks:
            recs.append(
                {
                    "icon": "📋",
                    "priority": "urgent",
                    "title": "Liideiltä puuttuu tehtäviä",
                    "body": f"{leads_without_tasks} aktiivisella liidillä ei ole avointa tehtävää.",
                    "action_url": "/tasks",
                    "action_label": "Luo tehtäviä",
                }
            )
        if high_score_no_contact:
            recs.append(
                {
                    "icon": "🔥",
                    "priority": "hot",
                    "title": "Korkean score:n liidit ilman kontaktia",
                    "body": f"{high_score_no_contact} liidiä scorella 80+ odottaa ensimmäistä yhteydenottoa.",
                    "action_url": "/leads?sort=score&dir=desc",
                    "action_label": "Avaa liidilista",
                }
            )
        if proposals_unviewed_sent:
            recs.append(
                {
                    "icon": "📨",
                    "priority": "",
                    "title": "Tarjouksia lähetetty ilman avauksia",
                    "body": f"{proposals_unviewed_sent} tarjous on lähetetty yli 7 päivää sitten, mutta ei avattu.",
                    "action_url": "/proposals?filter=active",
                    "action_label": "Avaa tarjoukset",
                }
            )
    elif context == "proposals":
        if proposals_viewed_not_accepted:
            recs.append(
                {
                    "icon": "👀",
                    "priority": "hot",
                    "title": "Tarjouksia katsottu, mutta ei hyväksytty",
                    "body": f"{proposals_viewed_not_accepted} tarjousta on tilassa 'viewed'.",
                    "action_url": "/proposals?filter=active",
                    "action_label": "Seuraa tarjouksia",
                }
            )
        if expiring_proposals:
            recs.append(
                {
                    "icon": "⏳",
                    "priority": "urgent",
                    "title": "Tarjouksia vanhenee pian",
                    "body": f"{expiring_proposals} aktiivista tarjousta vanhenee 7 päivän sisällä.",
                    "action_url": "/proposals?filter=active",
                    "action_label": "Uusi ennen vanhenemista",
                }
            )
    else:
        if overdue_tasks:
            recs.append(
                {
                    "icon": "⏰",
                    "priority": "urgent",
                    "title": "Erääntyneet tehtävät",
                    "body": f"{overdue_tasks} tehtävää on erääntynyt.",
                    "action_url": "/tasks",
                    "action_label": "Näytä tehtävät",
                }
            )
        if no_contact_count:
            recs.append(
                {
                    "icon": "📞",
                    "priority": "",
                    "title": "Liidit ilman kontaktia",
                    "body": f"{no_contact_count} aktiivisella liidillä ei ole vielä yhteydenottoa.",
                    "action_url": "/leads",
                    "action_label": "Avaa liidit",
                }
            )
        if expiring_proposals:
            recs.append(
                {
                    "icon": "🧾",
                    "priority": "hot",
                    "title": "Tarjoukset vaativat toimenpiteitä",
                    "body": f"{expiring_proposals} tarjousta vanhenee pian.",
                    "action_url": "/proposals?filter=active",
                    "action_label": "Tarkista tarjoukset",
                }
            )

    return recs[:5]


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _risk_recommendation_text(value: str | None) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    risk_terms = (
        "risk",
        "riski",
        "churn",
        "danger",
        "warning",
        "varoitus",
        "stalled",
        "without contact",
        "ei kontaktia",
    )
    return any(term in text for term in risk_terms)


def _build_ai_alerts(organization_id: int) -> list[dict]:
    from app.analytics.models import PredictionLog
    from app.tasks.models import Task

    now = datetime.now(timezone.utc)
    alerts: list[dict] = []
    seen_lead_ids: set[int] = set()

    overdue_tasks = (
        Task.query.filter(
            Task.organization_id == organization_id,
            Task.status.in_(("pending", "in_progress")),
            Task.due_date < now,
        )
        .order_by(Task.due_date.asc())
        .limit(5)
        .all()
    )
    for task in overdue_tasks:
        if not task.lead_id:
            continue
        lead = task.lead
        if lead is None:
            continue
        due_date = _to_utc(task.due_date)
        overdue_days = max(1, (now - due_date).days) if due_date else 1
        lead_name = lead.display_name
        alerts.append(
            {
                "type": "overdue_task",
                "task_id": task.id,
                "lead_id": lead.id,
                "lead_name": lead_name,
                "company": lead.company,
                "message": f"Follow-up myöhässä {overdue_days} pv",
                "reason": "Myöhässä oleva tehtävä voi heikentää konversiota.",
                "actions": ["view_lead", "complete_task"],
            }
        )
        seen_lead_ids.add(lead.id)
        if len(alerts) >= 5:
            return alerts

    first_stage = (
        PipelineStage.query.filter_by(organization_id=organization_id)
        .order_by(PipelineStage.order_index.asc(), PipelineStage.id.asc())
        .first()
    )
    if first_stage and len(alerts) < 5:
        has_non_created_activity = (
            db.session.query(Activity.lead_id.label("lead_id"))
            .filter(
                Activity.organization_id == organization_id,
                Activity.type != "created",
            )
            .subquery()
        )
        cutoff = now - timedelta(hours=24)
        new_leads = (
            Lead.query.filter(
                Lead.organization_id == organization_id,
                Lead.status == "active",
                Lead.stage_id == first_stage.id,
                Lead.created_at >= cutoff,
                ~Lead.id.in_(db.session.query(has_non_created_activity.c.lead_id)),
            )
            .order_by(Lead.created_at.desc())
            .limit(5)
            .all()
        )
        for lead in new_leads:
            if lead.id in seen_lead_ids:
                continue
            alerts.append(
                {
                    "type": "new_lead",
                    "lead_id": lead.id,
                    "lead_name": lead.display_name,
                    "company": lead.company,
                    "message": "Uusi liidi saapunut - ei vielä käsitelty",
                    "reason": "Nopea ensikontakti kasvattaa vastausastetta.",
                    "actions": ["view_lead", "create_task"],
                }
            )
            seen_lead_ids.add(lead.id)
            if len(alerts) >= 5:
                return alerts

    if len(alerts) < 5:
        last_activity_subquery = (
            db.session.query(
                Activity.lead_id.label("lead_id"),
                func.max(Activity.created_at).label("last_activity_at"),
            )
            .filter(Activity.organization_id == organization_id)
            .group_by(Activity.lead_id)
            .subquery()
        )
        hot_cutoff = now - timedelta(days=7)
        hot_leads = (
            Lead.query.outerjoin(
                last_activity_subquery, last_activity_subquery.c.lead_id == Lead.id
            )
            .filter(
                Lead.organization_id == organization_id,
                Lead.status == "active",
                Lead.score.isnot(None),
                Lead.score >= 70,
                ~Lead.id.in_(seen_lead_ids),
                func.coalesce(Lead.last_contacted_at, last_activity_subquery.c.last_activity_at)
                <= hot_cutoff,
            )
            .order_by(Lead.score.desc(), Lead.updated_at.desc())
            .limit(5)
            .all()
        )
        for lead in hot_leads:
            baseline = _to_utc(lead.last_contacted_at)
            if baseline is None:
                baseline = (
                    db.session.query(func.max(Activity.created_at))
                    .filter(
                        Activity.organization_id == organization_id,
                        Activity.lead_id == lead.id,
                    )
                    .scalar()
                )
                baseline = _to_utc(baseline)
            days_no_contact = max(7, (now - baseline).days) if baseline else 7
            alerts.append(
                {
                    "type": "hot_lead_no_contact",
                    "lead_id": lead.id,
                    "lead_name": lead.display_name,
                    "company": lead.company,
                    "score": lead.score,
                    "days_no_contact": days_no_contact,
                    "message": f"Kuuma liidi - ei kontaktia {days_no_contact} pv",
                    "reason": "Korkean pistemäärän liidi kylmenee ilman yhteydenottoa.",
                    "actions": ["view_lead", "send_email"],
                }
            )
            seen_lead_ids.add(lead.id)
            if len(alerts) >= 5:
                return alerts

    if len(alerts) < 5:
        latest_prediction = aliased(PredictionLog)
        latest_prediction_id = (
            db.session.query(
                PredictionLog.lead_id.label("lead_id"),
                func.max(PredictionLog.id).label("max_id"),
            )
            .filter(PredictionLog.organization_id == organization_id)
            .group_by(PredictionLog.lead_id)
            .subquery()
        )
        risk_rows = (
            db.session.query(Lead, latest_prediction.recommendation)
            .join(latest_prediction_id, latest_prediction_id.c.lead_id == Lead.id)
            .join(
                latest_prediction,
                latest_prediction.id == latest_prediction_id.c.max_id,
            )
            .filter(
                Lead.organization_id == organization_id,
                Lead.status == "active",
                ~Lead.id.in_(seen_lead_ids),
            )
            .order_by(latest_prediction.created_at.desc())
            .limit(10)
            .all()
        )
        for lead, recommendation in risk_rows:
            if not _risk_recommendation_text(recommendation):
                continue
            alerts.append(
                {
                    "type": "ai_risk",
                    "lead_id": lead.id,
                    "lead_name": lead.display_name,
                    "company": lead.company,
                    "message": "AI havaitsi riskin - liidi vaatii huomiota",
                    "reason": (recommendation or "AI-analyysi nosti riskisignaalin.")[:160],
                    "actions": ["view_lead", "create_task"],
                }
            )
            if len(alerts) >= 5:
                break

    return alerts[:5]


@analytics_bp.before_request
@login_required
def block_api_client():
    _require_ui_role()


@analytics_bp.route("/api/ai/recommendations", methods=["GET"])
def ai_recommendations():
    organization_id = _optional_organization_id()
    if organization_id is None:
        return json_success({"recommendations": []})
    context = (request.args.get("context") or "dashboard").strip().lower()
    recommendations = _ai_recommendations_for_context(organization_id, context)
    return json_success({"recommendations": recommendations})


@analytics_bp.route("/api/ai/alerts", methods=["GET"])
def ai_alerts():
    organization_id = _optional_organization_id()
    if organization_id is None:
        return json_success({"alerts": []})
    return json_success({"alerts": _build_ai_alerts(organization_id)})


@analytics_bp.route("/api/dashboard/today", methods=["GET"])
def dashboard_today():
    """Today's priority cards — hot leads, overdue tasks, unprocessed, AI recommendations."""
    organization_id = _optional_organization_id()
    if organization_id is None:
        return json_success(
            {
                "hot_leads": [],
                "overdue_tasks": [],
                "unprocessed_leads": [],
                "ai_recommendations": [],
            }
        )
    data = DashboardTodayService.get_today_data(organization_id)
    return json_success(data)


@analytics_bp.route("/api/dashboard/ai-worklist", methods=["GET"])
def dashboard_ai_worklist():
    """Rule-based ranked worklist for today."""
    organization_id = _optional_organization_id()
    if organization_id is None:
        return json_success({"items": []})
    items = DashboardTodayService.get_ai_worklist(organization_id)
    return json_success({"items": items})


@analytics_bp.route("/api/dashboard/metrics", methods=["GET"])
def dashboard_metrics():
    organization_id = _optional_organization_id()
    if organization_id is None:
        return json_success(
            {
                "new_leads_7d": 0,
                "new_leads_delta_pct": 0,
                "hot_leads": 0,
                "tasks_today": 0,
                "overdue_tasks": 0,
                "pipeline_value": None,
            }
        )
    return json_success(DashboardTodayService.get_dashboard_metrics(organization_id))


@analytics_bp.route("/api/dashboard/pipeline-distribution", methods=["GET"])
def dashboard_pipeline_distribution():
    """Return pipeline distribution for the current organization as JSON."""
    organization_id = _optional_organization_id()
    if organization_id is None:
        return json_success(
            {
                "labels": [],
                "counts": [],
                "percentages": [],
                "total": 0,
            }
        )

    rows = (
        db.session.query(PipelineStage.name, Lead.status, func.count(Lead.id))
        .join(Lead, Lead.stage_id == PipelineStage.id)
        .filter(
            Lead.organization_id == organization_id,
            PipelineStage.organization_id == organization_id,
            Lead.status.in_(("active", "won", "lost")),
        )
        .group_by(PipelineStage.name, Lead.status)
        .all()
    )

    buckets = {
        "Uusi liidi": 0,
        "Kontaktoitu": 0,
        "Kvalifioitu": 0,
        "Tarjous lähetetty": 0,
        "Voitettu": 0,
        "Hävitty": 0,
    }

    for stage_name, status, count in rows:
        label = _pipeline_bucket_label(stage_name, status)
        if label and label in buckets:
            buckets[label] += int(count or 0)

    # Remove empty buckets from response but keep deterministic order
    ordered_labels = [
        key for key in buckets.keys() if buckets[key] > 0
    ]
    total = sum(buckets.values())

    if total <= 0 or not ordered_labels:
        return json_success(
            {
                "labels": [],
                "counts": [],
                "percentages": [],
                "total": 0,
            }
        )

    counts = [buckets[label] for label in ordered_labels]
    percentages = [
        round((count / total) * 100.0, 1) if count else 0.0 for count in counts
    ]

    return json_success(
        {
            "labels": ordered_labels,
            "counts": counts,
            "percentages": percentages,
            "total": total,
        }
    )


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
    lost_reason_counts = []
    if organization_id is not None and not range_error:
        loaders = {
            "pipeline": AnalyticsService.get_pipeline_report,
            "source": AnalyticsService.get_source_report,
            "team": AnalyticsService.get_team_report,
            "ai": AnalyticsService.get_ai_report,
        }
        report_data = loaders[report_type](organization_id, start_dt, end_dt)
        lost_reason_counts = AnalyticsService.get_lost_reason_counts(organization_id)

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
        lost_reason_counts=lost_reason_counts,
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
