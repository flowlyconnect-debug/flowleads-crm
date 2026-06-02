from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.leads.models import ACTIVITY_TYPES, Lead
from app.leads.services import LeadService, LeadServiceError, get_lead_for_org
from app.tasks.models import (
    AUTO_TASK_TRIGGERS,
    TASK_PRIORITIES,
    TASK_STATUSES,
    TASK_TYPES,
    Task,
)
from app.tasks.settings import get_organization_settings
from app.users.models import User

logger = logging.getLogger(__name__)


class TaskServiceError(Exception):
    def __init__(self, message: str, code: str = "task_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_task_for_org(task_id: int, organization_id: int) -> Task:
    task = (
        Task.query.filter_by(id=task_id, organization_id=organization_id)
        .options(joinedload(Task.lead), joinedload(Task.assignee))
        .first()
    )
    if not task:
        raise TaskServiceError("Task not found.", "not_found")
    return task


def _validate_task_fields(data: dict) -> None:
    task_type = data.get("type", "follow_up")
    if task_type not in TASK_TYPES:
        raise TaskServiceError("Invalid task type.", "validation_error")

    priority = data.get("priority", "normal")
    if priority not in TASK_PRIORITIES:
        raise TaskServiceError("Invalid task priority.", "validation_error")

    status = data.get("status", "pending")
    if status not in TASK_STATUSES:
        raise TaskServiceError("Invalid task status.", "validation_error")

    title = (data.get("title") or "").strip()
    if not title:
        raise TaskServiceError("Title is required.", "validation_error")
    if len(title) > 200:
        raise TaskServiceError("Title must be at most 200 characters.", "validation_error")


def _validate_assignee(assigned_to: int, organization_id: int) -> User:
    user = User.query.filter_by(id=assigned_to, organization_id=organization_id, is_active=True).first()
    if not user:
        raise TaskServiceError("Assigned user not found in organization.", "invalid_assignee")
    return user


def _start_of_day(dt: datetime | None = None) -> datetime:
    now = _ensure_tz(dt or _utc_now())
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _end_of_day(dt: datetime | None = None) -> datetime:
    now = _ensure_tz(dt or _utc_now())
    return now.replace(hour=23, minute=59, second=59, microsecond=999999)


def _log_task_activity(
    task: Task,
    user_id: int | None,
    activity_type: str,
    *,
    content: str | None = None,
    metadata: dict | None = None,
) -> None:
    if activity_type not in ACTIVITY_TYPES:
        raise TaskServiceError("Invalid activity type.", "invalid_activity")
    if not task.lead_id:
        return
    meta = dict(metadata or {})
    meta["task_id"] = task.id
    meta["task_title"] = task.title
    LeadService.log_activity(
        task.lead_id,
        user_id,
        activity_type,
        content=content,
        metadata=meta,
    )


class TaskService:
    @staticmethod
    def create(
        data: dict,
        organization_id: int,
        user_id: int | None,
        *,
        lead_id: int | None = None,
        actor_role: str | None = None,
    ) -> Task:
        _validate_task_fields(data)

        if lead_id is not None:
            get_lead_for_org(lead_id, organization_id)

        assigned_to = data.get("assigned_to")
        if assigned_to is None:
            if user_id is None:
                raise TaskServiceError("assigned_to is required.", "validation_error")
            assigned_to = user_id
        else:
            assigned_to = int(assigned_to)

        if actor_role == "user" and user_id and assigned_to != user_id:
            raise TaskServiceError(
                "You cannot assign tasks to other users.", "forbidden_assign"
            )

        _validate_assignee(assigned_to, organization_id)

        due_date = data.get("due_date")
        if due_date is None:
            raise TaskServiceError("due_date is required.", "validation_error")
        if isinstance(due_date, str):
            try:
                due_date = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
            except ValueError:
                raise TaskServiceError("Invalid due_date format.", "validation_error") from None
        due_date = _ensure_tz(due_date)

        reminder_at = data.get("reminder_at")
        if isinstance(reminder_at, str):
            try:
                reminder_at = datetime.fromisoformat(reminder_at.replace("Z", "+00:00"))
            except ValueError:
                raise TaskServiceError("Invalid reminder_at format.", "validation_error") from None
        if reminder_at is not None:
            reminder_at = _ensure_tz(reminder_at)

        task = Task(
            organization_id=organization_id,
            lead_id=lead_id,
            assigned_to=assigned_to,
            created_by=user_id,
            title=(data.get("title") or "").strip(),
            description=(data.get("description") or "").strip() or None,
            type=data.get("type", "follow_up"),
            status=data.get("status", "pending"),
            priority=data.get("priority", "normal"),
            due_date=due_date,
            reminder_at=reminder_at,
            reminder_sent=False,
        )
        db.session.add(task)
        db.session.flush()

        _log_task_activity(
            task,
            user_id,
            "task_created",
            content=task.title,
            metadata={"type": task.type, "priority": task.priority},
        )
        return task

    @staticmethod
    def complete(task_id: int, organization_id: int, user_id: int | None) -> Task:
        task = get_task_for_org(task_id, organization_id)
        if task.status == "completed":
            return task
        if task.status == "cancelled":
            raise TaskServiceError("Cannot complete a cancelled task.", "validation_error")

        task.status = "completed"
        task.completed_at = _utc_now()
        task.updated_at = _utc_now()
        db.session.flush()

        _log_task_activity(
            task,
            user_id,
            "task_completed",
            content=task.title,
        )
        return task

    @staticmethod
    def get_due_today(user_id: int, organization_id: int) -> list[Task]:
        start = _start_of_day()
        end = _end_of_day()
        return (
            Task.query.filter(
                Task.organization_id == organization_id,
                Task.assigned_to == user_id,
                Task.status.in_(("pending", "in_progress")),
                Task.due_date >= start,
                Task.due_date <= end,
            )
            .options(joinedload(Task.lead))
            .order_by(Task.due_date.asc())
            .all()
        )

    @staticmethod
    def get_overdue(organization_id: int, *, user_id: int | None = None) -> list[Task]:
        now = _utc_now()
        query = Task.query.filter(
            Task.organization_id == organization_id,
            Task.status.in_(("pending", "in_progress")),
            Task.due_date < now,
        )
        if user_id is not None:
            query = query.filter(Task.assigned_to == user_id)
        return (
            query.options(joinedload(Task.lead))
            .order_by(Task.due_date.asc())
            .all()
        )

    @staticmethod
    def count_overdue(organization_id: int, user_id: int) -> int:
        now = _utc_now()
        return Task.query.filter(
            Task.organization_id == organization_id,
            Task.assigned_to == user_id,
            Task.status.in_(("pending", "in_progress")),
            Task.due_date < now,
        ).count()

    @staticmethod
    def get_for_lead(lead_id: int, organization_id: int) -> list[Task]:
        get_lead_for_org(lead_id, organization_id)
        return (
            Task.query.filter_by(lead_id=lead_id, organization_id=organization_id)
            .options(joinedload(Task.assignee))
            .order_by(Task.status.asc(), Task.due_date.asc())
            .all()
        )

    @staticmethod
    def list_tasks(
        organization_id: int,
        *,
        tab: str = "all",
        task_type: str | None = None,
        priority: str | None = None,
        assigned_to: int | None = None,
    ) -> list[Task]:
        query = Task.query.filter(Task.organization_id == organization_id)
        if assigned_to is not None:
            query = query.filter(Task.assigned_to == assigned_to)

        if task_type and task_type in TASK_TYPES:
            query = query.filter(Task.type == task_type)
        if priority and priority in TASK_PRIORITIES:
            query = query.filter(Task.priority == priority)

        now = _utc_now()
        if tab == "today":
            start = _start_of_day()
            end = _end_of_day()
            query = query.filter(
                Task.status.in_(("pending", "in_progress")),
                Task.due_date >= start,
                Task.due_date <= end,
            )
        elif tab == "week":
            start = _start_of_day()
            end = _end_of_day(now + timedelta(days=7))
            query = query.filter(
                Task.status.in_(("pending", "in_progress")),
                Task.due_date >= start,
                Task.due_date <= end,
            )
        elif tab == "overdue":
            query = query.filter(
                Task.status.in_(("pending", "in_progress")),
                Task.due_date < now,
            )
        elif tab == "all":
            query = query.filter(Task.status.in_(("pending", "in_progress", "completed")))

        return (
            query.options(joinedload(Task.lead), joinedload(Task.assignee))
            .order_by(Task.due_date.asc())
            .all()
        )

    @staticmethod
    def get_recent(organization_id: int, user_id: int, limit: int = 5) -> list[Task]:
        return (
            Task.query.filter(
                Task.organization_id == organization_id,
                Task.assigned_to == user_id,
            )
            .options(joinedload(Task.lead))
            .order_by(Task.updated_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def send_reminders() -> int:
        """Send due task reminders. Returns count sent. Never raises."""
        from app.tasks.reminders import send_task_reminder_email

        now = _utc_now()
        tasks = (
            Task.query.filter(
                Task.reminder_at.isnot(None),
                Task.reminder_at <= now,
                Task.reminder_sent.is_(False),
                Task.status == "pending",
            )
            .options(
                joinedload(Task.lead),
                joinedload(Task.assignee),
            )
            .all()
        )

        sent = 0
        for task in tasks:
            try:
                send_task_reminder_email(task)
                task.reminder_sent = True
                task.updated_at = _utc_now()
                _log_task_activity(
                    task,
                    None,
                    "task_reminder_sent",
                    content=task.title,
                )
                db.session.commit()
                sent += 1
            except Exception:
                logger.exception("Failed to send reminder for task %s", task.id)
                db.session.rollback()
        return sent

    @staticmethod
    def create_auto_tasks(lead: Lead, trigger_type: str) -> Task | None:
        if trigger_type not in AUTO_TASK_TRIGGERS:
            raise TaskServiceError("Invalid auto-task trigger.", "validation_error")

        settings = get_organization_settings(lead.organization_id)

        if trigger_type == "new_lead":
            if not settings.auto_task_on_new_lead:
                return None
            assignee_id = lead.assigned_to
            if assignee_id is None:
                admin = (
                    User.query.filter_by(
                        organization_id=lead.organization_id,
                        role="admin",
                        is_active=True,
                    )
                    .order_by(User.id.asc())
                    .first()
                )
                assignee_id = admin.id if admin else None
            if assignee_id is None:
                return None
            due = _utc_now() + timedelta(days=1)
            return TaskService.create(
                {
                    "title": "Ota yhteyttä",
                    "type": "follow_up",
                    "priority": "normal",
                    "due_date": due,
                    "assigned_to": assignee_id,
                },
                lead.organization_id,
                None,
                lead_id=lead.id,
            )

        if trigger_type == "no_contact":
            days = settings.auto_task_no_contact_days or 14
            if lead.last_contacted_at:
                last = _ensure_tz(lead.last_contacted_at)
                if (_utc_now() - last).days < days:
                    return None
            elif (_utc_now() - _ensure_tz(lead.created_at)).days < days:
                return None

            assignee_id = lead.assigned_to
            if not assignee_id:
                return None
            existing = Task.query.filter(
                Task.organization_id == lead.organization_id,
                Task.lead_id == lead.id,
                Task.title == "Seuraa liidiä — ei yhteyttä",
                Task.status.in_(("pending", "in_progress")),
            ).first()
            if existing:
                return None

            return TaskService.create(
                {
                    "title": "Seuraa liidiä — ei yhteyttä",
                    "type": "follow_up",
                    "priority": "high",
                    "due_date": _utc_now() + timedelta(days=1),
                    "assigned_to": assignee_id,
                },
                lead.organization_id,
                None,
                lead_id=lead.id,
            )

        if trigger_type == "stage_change":
            if not settings.auto_task_stage_change:
                return None
            if not lead.stage or lead.stage.name not in {"Tarjous lähetetty", "Proposal Sent"}:
                return None
            assignee_id = lead.assigned_to
            if not assignee_id:
                return None
            return TaskService.create(
                {
                    "title": "Seuraa tarjousta",
                    "type": "follow_up",
                    "priority": "normal",
                    "due_date": _utc_now() + timedelta(days=3),
                    "assigned_to": assignee_id,
                },
                lead.organization_id,
                None,
                lead_id=lead.id,
            )

        return None

    @staticmethod
    def run_no_contact_auto_tasks() -> int:
        """Create no-contact auto tasks for eligible leads. Returns count created."""
        created = 0
        org_ids = db.session.query(Lead.organization_id).distinct().all()
        for (org_id,) in org_ids:
            settings = get_organization_settings(org_id)
            days = settings.auto_task_no_contact_days or 14
            cutoff = _utc_now() - timedelta(days=days)
            leads = Lead.query.filter(
                Lead.organization_id == org_id,
                Lead.status == "active",
                or_(
                    and_(Lead.last_contacted_at.is_(None), Lead.created_at <= cutoff),
                    Lead.last_contacted_at <= cutoff,
                ),
            ).all()
            for lead in leads:
                try:
                    task = TaskService.create_auto_tasks(lead, "no_contact")
                    if task:
                        db.session.commit()
                        created += 1
                    else:
                        db.session.rollback()
                except Exception:
                    logger.exception("Auto-task no_contact failed for lead %s", lead.id)
                    db.session.rollback()
        return created
