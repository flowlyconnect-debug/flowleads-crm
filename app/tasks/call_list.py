"""Soittolista: the day's phone calls in order, with the result of each call
recorded in one click.

A call is an open `Task` of type "call". Transfers on the Kohteet page can put
the new leads on the list (`prepare_call_tasks`): the automatic "Ota yhteyttä"
task of a new lead becomes the call, so the lead does not get two tasks.
`record_call_outcome` logs the call on the lead and moves the task on.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.leads.models import Lead
from app.leads.services import LeadService
from app.listings.pts import pts_from_text
from app.tasks.models import Task
from app.tasks.services import TaskService, TaskServiceError, get_task_for_org

HELSINKI = ZoneInfo("Europe/Helsinki")
OPEN_STATUSES = ("pending", "in_progress")
AUTO_TASK_TITLE = "Ota yhteyttä"  # TaskService.create_auto_tasks("new_lead")

# outcome -> (label logged on the lead, what happens to the task)
OUTCOMES: dict[str, str] = {
    "no_answer": "Ei vastannut",
    "offer": "Pyysi tarjouksen",
    "meeting": "Tapaaminen sovittu",
    "not_interested": "Ei kiinnostunut",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def end_of_today(now: datetime | None = None) -> datetime:
    """Midnight tonight in Finland, as UTC."""
    local = _aware(now or _utc_now()).astimezone(HELSINKI)
    tomorrow = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=HELSINKI)
    return tomorrow.astimezone(timezone.utc)


def next_morning(now: datetime | None = None, *, days: int = 1) -> datetime:
    """9.00 Finnish time `days` days from now, as UTC."""
    local = _aware(now or _utc_now()).astimezone(HELSINKI)
    target = datetime.combine(local.date() + timedelta(days=days), time(9, 0), tzinfo=HELSINKI)
    return target.astimezone(timezone.utc)


def call_title(lead: Lead) -> str:
    return f"Soita: {lead.company or lead.display_name}"[:200]


def prepare_call_tasks(
    organization_id: int, lead_ids, *, user_id: int, now: datetime | None = None
) -> dict:
    """Put the given leads on the call list for today. A lead without a phone
    number is skipped, and one that already has an open call is left as it is.
    Returns counts; the caller commits."""
    now = now or _utc_now()
    # due today, at the end of the day, so the call is not "overdue" on the
    # Tehtävät page the moment it is created
    due = end_of_today(now) - timedelta(minutes=1)
    result = {"added": 0, "no_phone": 0, "already": 0}
    if not lead_ids:
        return result
    leads = Lead.query.filter(Lead.organization_id == organization_id, Lead.id.in_(list(lead_ids))).all()
    for lead in leads:
        if not (lead.phone or "").strip():
            result["no_phone"] += 1
            continue
        open_tasks = Task.query.filter(
            Task.organization_id == organization_id,
            Task.lead_id == lead.id,
            Task.status.in_(OPEN_STATUSES),
        ).all()
        if any(task.type == "call" for task in open_tasks):
            result["already"] += 1
            continue
        auto = next(
            (t for t in open_tasks if t.type == "follow_up" and t.title == AUTO_TASK_TITLE and t.created_by is None),
            None,
        )
        if auto is not None:
            auto.type = "call"
            auto.title = call_title(lead)
            auto.due_date = due
            auto.updated_at = now
        else:
            TaskService.create(
                {
                    "title": call_title(lead),
                    "type": "call",
                    "priority": "normal",
                    "due_date": due,
                    "assigned_to": lead.assigned_to or user_id,
                },
                organization_id,
                user_id,
                lead_id=lead.id,
            )
        result["added"] += 1
    db.session.flush()
    return result


def call_list(organization_id: int, *, user_id: int | None, now: datetime | None = None) -> dict:
    """Open calls due today or earlier (oldest first) and how many are coming
    later. `user_id=None` lists the whole team's calls."""
    query = Task.query.filter(
        Task.organization_id == organization_id,
        Task.type == "call",
        Task.status.in_(OPEN_STATUSES),
    )
    if user_id is not None:
        query = query.filter(Task.assigned_to == user_id)
    limit = end_of_today(now)
    due = (
        query.filter(Task.due_date < limit)
        .options(joinedload(Task.lead))
        .order_by(Task.due_date.asc(), Task.id.asc())
        .all()
    )
    upcoming = query.filter(Task.due_date >= limit).count()
    return {"due": due, "upcoming": upcoming}


def count_due_calls(organization_id: int, user_id: int, now: datetime | None = None) -> int:
    return Task.query.filter(
        Task.organization_id == organization_id,
        Task.type == "call",
        Task.status.in_(OPEN_STATUSES),
        Task.assigned_to == user_id,
        Task.due_date < end_of_today(now),
    ).count()


def call_row(task: Task, now: datetime | None = None) -> dict:
    """What the call list shows about a call: the lead's catalogue data when it
    came from the Kohteet page, and whether the call is from an earlier day."""
    lead = task.lead
    start_of_today = end_of_today(now) - timedelta(days=1)
    meta = (lead.ai_contact_info if lead is not None and isinstance(lead.ai_contact_info, dict) else {}) or {}
    person = (meta.get("isannoitsija_henkilo") or "").strip()
    manager = (meta.get("isannoitsija_yritys") or "").strip()
    return {
        "task": task,
        "lead": lead,
        "contact": ", ".join(p for p in (person, manager) if p) or (lead.title if lead else "") or "",
        "phone": (lead.phone or "").strip() if lead else "",
        "renovation": (meta.get("remonttityyppi") or "").strip(),
        "timing": str(meta.get("remontin_ajankohta") or "").strip(),
        "city": (meta.get("kaupunki") or "").strip(),
        "late": _aware(task.due_date) < start_of_today,
        "pts": pts_from_text((meta.get("remonttityyppi") or "").strip(), str(meta.get("remontin_ajankohta") or "")),
    }


def record_call_outcome(
    task_id: int,
    organization_id: int,
    *,
    user_id: int,
    outcome: str,
    note: str | None = None,
    now: datetime | None = None,
) -> Task:
    """Log the call on the lead and move the task on:
    no answer -> the same call tomorrow morning; offer -> done, plus a
    "Tee tarjous" task for tomorrow; meeting / not interested -> done."""
    if outcome not in OUTCOMES:
        raise TaskServiceError("Invalid call outcome.", "validation_error")
    now = now or _utc_now()
    task = get_task_for_org(task_id, organization_id)
    if task.type != "call" or task.status not in OPEN_STATUSES:
        raise TaskServiceError("This call is already handled.", "validation_error")
    label = OUTCOMES[outcome]
    note = (note or "").strip()[:1000] or None
    if task.lead_id:
        LeadService.log_activity(
            task.lead_id,
            user_id,
            "call",
            content=f"{label}: {note}" if note else label,
            metadata={"outcome": outcome, "task_id": task.id},
        )
        lead = db.session.get(Lead, task.lead_id)
        if lead is not None and outcome != "no_answer":
            lead.last_contacted_at = now
    if outcome == "no_answer":
        task.due_date = next_morning(now)
        task.updated_at = now
        db.session.flush()
        return task
    TaskService.complete(task.id, organization_id, user_id)
    if outcome == "offer" and task.lead_id:
        TaskService.create(
            {
                "title": "Tee tarjous",
                "type": "follow_up",
                "priority": "high",
                "due_date": next_morning(now),
                "assigned_to": task.assigned_to,
            },
            organization_id,
            user_id,
            lead_id=task.lead_id,
        )
    db.session.flush()
    return task
