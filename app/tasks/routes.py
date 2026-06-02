from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app.core.errors import json_error, json_success, wants_json_response
from app.extensions import db
from app.leads.models import Lead
from app.leads.permissions import can_assign_to_others, resolve_organization_id
from app.tasks.forms import QuickTaskForm
from app.tasks.models import Task
from app.tasks.models import TASK_PRIORITIES, TASK_TYPES
from app.tasks.services import TaskService, TaskServiceError, get_task_for_org
from app.users.models import User

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")

UI_ROLES = ("superadmin", "admin", "user")


def _require_ui_role():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in UI_ROLES:
        abort(403)


def _org_users(organization_id: int):
    return User.query.filter_by(organization_id=organization_id, is_active=True).order_by(
        User.email
    ).all()


def _org_query_suffix(organization_id: int) -> dict:
    if current_user.is_superadmin():
        return {"organization_id": organization_id}
    return {}


def _to_utc(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@tasks_bp.before_request
@login_required
def block_api_client():
    _require_ui_role()


@tasks_bp.context_processor
def inject_overdue_count():
    if not current_user.is_authenticated or current_user.role not in UI_ROLES:
        return {}
    try:
        org_id = current_user.organization_id
        if current_user.is_superadmin():
            org_param = request.args.get("organization_id")
            if org_param:
                org_id = int(org_param)
        if org_id is None:
            return {"nav_overdue_count": 0}
        count = TaskService.count_overdue(org_id, current_user.id)
        return {"nav_overdue_count": count}
    except Exception:
        return {"nav_overdue_count": 0}


@tasks_bp.route("")
def list_tasks():
    organization_id = resolve_organization_id()
    if can_assign_to_others():
        raw_assigned = request.args.get("assigned_to", str(current_user.id))
        if raw_assigned == "all":
            assigned_filter = None
        else:
            try:
                assigned_filter = int(raw_assigned)
            except (TypeError, ValueError):
                assigned_filter = current_user.id
    else:
        assigned_filter = current_user.id

    now = datetime.now(timezone.utc)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_today = start_today + timedelta(days=1)
    start_week = start_today - timedelta(days=start_today.weekday())
    end_week = start_week + timedelta(days=7)
    tomorrow = end_today

    open_query = Task.query.filter(
        Task.organization_id == organization_id,
        Task.status.in_(("pending", "in_progress")),
    )
    if assigned_filter is not None:
        open_query = open_query.filter(Task.assigned_to == assigned_filter)
    open_tasks = open_query.options(joinedload(Task.lead)).order_by(Task.due_date.asc()).all()

    overdue_tasks = []
    today_tasks = []
    week_tasks = []
    later_tasks = []
    for task in open_tasks:
        due_date = _to_utc(task.due_date)
        if due_date is None:
            continue
        if due_date < start_today:
            overdue_tasks.append(task)
        elif due_date < end_today:
            today_tasks.append(task)
        elif tomorrow <= due_date < end_week:
            week_tasks.append(task)
        elif due_date >= end_week:
            later_tasks.append(task)

    completed_query = Task.query.filter(
        Task.organization_id == organization_id,
        Task.status == "completed",
    )
    if assigned_filter is not None:
        completed_query = completed_query.filter(Task.assigned_to == assigned_filter)
    completed_tasks = (
        completed_query.options(joinedload(Task.lead))
        .order_by(Task.completed_at.desc(), Task.updated_at.desc())
        .limit(20)
        .all()
    )

    quick_form = QuickTaskForm()
    default_due = datetime.now(timezone.utc)
    quick_form.due_date.data = default_due.replace(tzinfo=None)

    users = _org_users(organization_id) if can_assign_to_others() else []
    leads = (
        Lead.query.filter_by(organization_id=organization_id)
        .order_by(Lead.updated_at.desc())
        .limit(200)
        .all()
    )
    return render_template(
        "tasks/list.html",
        quick_form=quick_form,
        task_types=TASK_TYPES,
        priorities=TASK_PRIORITIES,
        users=users,
        leads=leads,
        overdue_tasks=overdue_tasks,
        today_tasks=today_tasks,
        week_tasks=week_tasks,
        later_tasks=later_tasks,
        completed_tasks=completed_tasks,
        today_date=start_today.date(),
        week_start=start_week,
        week_end=end_week - timedelta(days=1),
        organization_id=organization_id,
        assigned_filter=assigned_filter,
        can_assign_others=can_assign_to_others(),
        org_query=_org_query_suffix(organization_id),
    )


@tasks_bp.route("", methods=["POST"])
def create_task():
    organization_id = resolve_organization_id()
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        lead_id = payload.get("lead_id")
        raw_due = payload.get("due_date")
        if not raw_due:
            raw_due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        task_data = {
            "title": payload.get("title") or "Follow-up",
            "type": payload.get("type") or "follow_up",
            "priority": payload.get("priority") or "normal",
            "due_date": raw_due,
            "assigned_to": payload.get("assigned_to") or current_user.id,
            "description": payload.get("description"),
        }
        try:
            task = TaskService.create(
                task_data,
                organization_id,
                current_user.id,
                lead_id=int(lead_id) if lead_id else None,
                actor_role=current_user.role,
            )
            db.session.commit()
            due = _to_utc(task.due_date)
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow = today + timedelta(days=1)
            week_end = today - timedelta(days=today.weekday()) + timedelta(days=7)
            section = "later"
            if due and due < today:
                section = "overdue"
            elif due and due < tomorrow:
                section = "today"
            elif due and due < week_end:
                section = "week"
            return json_success(
                {
                    "task": {
                        "id": task.id,
                        "title": task.title,
                        "lead_id": task.lead_id,
                        "lead_name": (
                            task.lead.company if task.lead and task.lead.company else (task.lead.display_name if task.lead else "")
                        ),
                        "status": task.status,
                        "priority": task.priority,
                        "due_date": task.due_date.isoformat() if task.due_date else None,
                        "due_time": due.strftime("%H:%M") if due else "",
                        "due_day": due.strftime("%d.%m.") if due else "",
                        "overdue_days": (today - due.replace(hour=0, minute=0, second=0, microsecond=0)).days if due and due < today else 0,
                        "lead_url": (
                            url_for("leads.detail", lead_id=task.lead_id, **_org_query_suffix(organization_id))
                            if task.lead_id
                            else None
                        ),
                        "complete_url": url_for("tasks.complete_task", task_id=task.id, **_org_query_suffix(organization_id)),
                        "section": section,
                    }
                },
                status=201,
            )
        except (TypeError, ValueError):
            db.session.rollback()
            return json_error("validation_error", "Invalid lead_id.", 400)
        except TaskServiceError as exc:
            db.session.rollback()
            return json_error(exc.code, exc.message, 404 if exc.code == "not_found" else 400)

    quick_form = QuickTaskForm()
    if not quick_form.validate_on_submit():
        flash("Invalid task data.", "danger")
        return redirect(url_for("tasks.list_tasks", tab="today", **_org_query_suffix(organization_id)))

    data = quick_form.to_service_data()
    data["assigned_to"] = current_user.id

    try:
        TaskService.create(
            data,
            organization_id,
            current_user.id,
            actor_role=current_user.role,
        )
        db.session.commit()
        flash("Task created.", "success")
    except TaskServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")

    return redirect(url_for("tasks.list_tasks", tab="today", **_org_query_suffix(organization_id)))


@tasks_bp.route("/<int:task_id>/complete", methods=["POST", "PATCH"])
def complete_task(task_id: int):
    organization_id = resolve_organization_id()
    wants_json = request.method == "PATCH" or wants_json_response() or request.is_json
    try:
        TaskService.complete(task_id, organization_id, current_user.id)
        db.session.commit()
        if wants_json:
            return json_success({"task_id": task_id, "status": "completed"})
        flash("Task completed.", "success")
    except TaskServiceError as exc:
        db.session.rollback()
        if wants_json:
            return json_error(exc.code, exc.message, 404 if exc.code == "not_found" else 400)
        flash(exc.message, "danger")

    next_url = request.referrer or url_for("tasks.list_tasks", **_org_query_suffix(organization_id))
    return redirect(next_url)

