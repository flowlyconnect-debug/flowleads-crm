from datetime import datetime, timezone

from flask import Blueprint, request
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.core.errors import json_error, json_success
from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.permissions import resolve_organization_id
from app.notifications.services import NotificationService, NotificationServiceError
from app.tasks.models import Task

from flask import request as flask_request

notifications_bp = Blueprint("notifications_api", __name__, url_prefix="/api")


def _ensure_utc(value: datetime | None) -> datetime | None:
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time_ago_label(created_at: datetime | None) -> str:
    ts = _ensure_utc(created_at)
    if not ts:
        return "—"
    now = datetime.now(timezone.utc)
    delta = now - ts
    minutes = int(max(delta.total_seconds(), 0) // 60)
    if minutes < 1:
        return "Juuri nyt"
    if minutes < 60:
        return f"{minutes} min sitten"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h sitten"
    days = hours // 24
    if days < 7:
        return f"{days} pv sitten"
    return ts.strftime("%d.%m.%Y")


def _activity_icon(activity_type: str | None) -> str:
    icon_map = {
        "note": "📝",
        "email_sent": "✉️",
        "email_opened": "👀",
        "email_clicked": "🔗",
        "call": "📞",
        "stage_changed": "🔁",
        "task_created": "📋",
        "task_completed": "✅",
        "meeting_scheduled": "📅",
        "proposal_sent": "📄",
        "proposal_viewed": "👁️",
        "proposal_accepted": "🎉",
    }
    return icon_map.get((activity_type or "").strip(), "•")


def _activity_label(activity: Activity) -> str:
    fallback = (activity.type or "aktiviteetti").replace("_", " ").strip().capitalize()
    return (activity.content or "").strip() or fallback


@notifications_bp.context_processor
def inject_notification_count():
    from flask_login import current_user

    if not current_user.is_authenticated:
        return {}
    try:
        org_id = current_user.organization_id
        if current_user.is_superadmin():
            org_param = flask_request.args.get("organization_id")
            if org_param:
                org_id = int(org_param)
        if org_id is None:
            return {"nav_unread_count": 0}
        from app.notifications.services import NotificationService

        return {
            "nav_unread_count": NotificationService.count_unread(
                current_user.id, org_id
            )
        }
    except Exception:
        return {"nav_unread_count": 0}


@notifications_bp.route("/notifications", methods=["GET"])
@login_required
def list_notifications():
    if current_user.organization_id is None and not current_user.is_superadmin():
        return json_error("forbidden", "Access denied.", 403)
    org_id = current_user.organization_id
    if current_user.is_superadmin():
        org_param = request.args.get("organization_id")
        if org_param:
            org_id = int(org_param)
    if org_id is None:
        return json_error("validation_error", "organization_id required.", 400)
    data = NotificationService.get_for_user(current_user.id, org_id)
    return json_success(data)


@notifications_bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id: int):
    org_id = current_user.organization_id
    if current_user.is_superadmin():
        org_param = request.args.get("organization_id")
        if request.is_json and request.json:
            org_param = org_param or request.json.get("organization_id")
        if org_param:
            org_id = int(org_param)
    if org_id is None:
        return json_error("validation_error", "organization_id required.", 400)
    try:
        NotificationService.mark_read(notification_id, current_user.id, org_id)
        db.session.commit()
        return json_success({"id": notification_id, "is_read": True})
    except NotificationServiceError as exc:
        db.session.rollback()
        return json_error(exc.code, exc.message, 404)


@notifications_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    org_id = current_user.organization_id
    if current_user.is_superadmin():
        org_param = request.args.get("organization_id")
        if request.is_json and request.json:
            org_param = org_param or request.json.get("organization_id")
        if org_param:
            org_id = int(org_param)
    if org_id is None:
        return json_error("validation_error", "organization_id required.", 400)
    count = NotificationService.mark_all_read(current_user.id, org_id)
    db.session.commit()
    return json_success({"marked_read": count})


@notifications_bp.route("/leads/<int:lead_id>/preview", methods=["GET"])
@login_required
def lead_preview(lead_id: int):
    organization_id = resolve_organization_id()
    lead = Lead.query.filter_by(id=lead_id, organization_id=organization_id).first()
    if not lead:
        return json_error("not_found", "Lead not found.", 404)

    activities = (
        Activity.query.filter_by(lead_id=lead.id, organization_id=organization_id)
        .order_by(Activity.created_at.desc())
        .limit(5)
        .all()
    )
    return json_success(
        {
            "id": lead.id,
            "first_name": lead.first_name or "",
            "last_name": lead.last_name or "",
            "company": lead.company or "",
            "email": lead.email or "",
            "phone": lead.phone or "",
            "stage_name": lead.stage.name if lead.stage else "—",
            "score": lead.score,
            "ai_summary": lead.ai_summary or "",
            "recent_activities": [
                {
                    "icon": _activity_icon(a.type),
                    "description": _activity_label(a),
                    "time_ago": _time_ago_label(a.created_at),
                }
                for a in activities
            ],
        }
    )


@notifications_bp.route("/search", methods=["GET"])
@login_required
def command_palette_search():
    organization_id = resolve_organization_id()
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return json_success({"leads": [], "tasks": []})

    like_query = f"%{query}%"
    leads = (
        Lead.query.filter(
            Lead.organization_id == organization_id,
            Lead.status != "archived",
            or_(
                Lead.company.ilike(like_query),
                Lead.first_name.ilike(like_query),
                Lead.last_name.ilike(like_query),
                Lead.email.ilike(like_query),
            ),
        )
        .order_by(Lead.updated_at.desc())
        .limit(5)
        .all()
    )
    tasks = (
        Task.query.filter(
            Task.organization_id == organization_id,
            Task.title.ilike(like_query),
            Task.status.in_(("pending", "in_progress")),
        )
        .order_by(Task.due_date.asc())
        .limit(5)
        .all()
    )
    return json_success(
        {
            "leads": [
                {
                    "id": lead.id,
                    "first_name": lead.first_name or "",
                    "last_name": lead.last_name or "",
                    "company": lead.company or "—",
                    "stage_name": lead.stage.name if lead.stage else "—",
                }
                for lead in leads
            ],
            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "due_label": _time_ago_label(task.due_date),
                }
                for task in tasks
            ],
        }
    )
