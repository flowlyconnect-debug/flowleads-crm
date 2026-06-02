from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.errors import json_error, json_success, wants_json_response
from app.extensions import db
from app.leads.permissions import can_assign_to_others, resolve_organization_id
from app.tasks.forms import QuickTaskForm
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
    tab = request.args.get("tab", "today")
    if tab not in ("today", "week", "all", "overdue"):
        tab = "today"

    task_type = request.args.get("type") or None
    priority = request.args.get("priority") or None
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

    tasks = TaskService.list_tasks(
        organization_id,
        tab=tab,
        task_type=task_type,
        priority=priority,
        assigned_to=assigned_filter,
    )

    quick_form = QuickTaskForm()
    default_due = datetime.now(timezone.utc) + timedelta(days=1)
    quick_form.due_date.data = default_due.replace(tzinfo=None)

    users = _org_users(organization_id) if can_assign_to_others() else []
    return render_template(
        "tasks/list.html",
        tasks=tasks,
        tab=tab,
        quick_form=quick_form,
        task_types=TASK_TYPES,
        priorities=TASK_PRIORITIES,
        users=users,
        organization_id=organization_id,
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
            return json_success(
                {
                    "task": {
                        "id": task.id,
                        "title": task.title,
                        "lead_id": task.lead_id,
                        "status": task.status,
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

