"""Daily sales command center — today's priorities and AI worklist."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, not_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.leads.models import Activity, Lead, PipelineStage
from app.leads.services import LeadService, get_default_stage
from app.proposals.models import Proposal
from app.tasks.models import Task

CLOSED_STAGE_NAMES = frozenset(
    {"Won", "Lost", "Closed Won", "Closed Lost", "Voitettu", "Hävitty"}
)
RISK_RECOMMENDATIONS = frozenset(
    {"Ota yhteyttä nyt", "Muistuta tarjouksesta", "Seuraa välittömästi"}
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def time_ago_fi(dt: datetime | None, *, now: datetime | None = None) -> str:
    if dt is None:
        return ""
    now = now or _utc_now()
    dt = _ensure_aware(dt)
    seconds = int((now - dt).total_seconds())
    if seconds < 60:
        return "Juuri nyt"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min sitten"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h sitten"
    days = hours // 24
    return f"{days} pv sitten"


def days_since(dt: datetime | None, *, now: datetime | None = None) -> int | None:
    if dt is None:
        return None
    now = now or _utc_now()
    dt = _ensure_aware(dt)
    return max(0, (now - dt).days)


def _active_lead_filter(organization_id: int):
    return (
        Lead.organization_id == organization_id,
        Lead.status == "active",
    )


def _exclude_closed_stages():
    return Lead.stage.has(PipelineStage.name.notin_(CLOSED_STAGE_NAMES))


def _last_activity_at(lead_id: int, organization_id: int) -> datetime | None:
    row = (
        Activity.query.filter_by(lead_id=lead_id, organization_id=organization_id)
        .order_by(Activity.created_at.desc())
        .first()
    )
    return row.created_at if row else None


def _days_since_contact(lead: Lead, organization_id: int, now: datetime) -> int | None:
    last = _last_activity_at(lead.id, organization_id)
    if last is None:
        last = lead.last_contacted_at
    return days_since(last, now=now)


def _score_badge_label(score: int | None) -> tuple[str, str]:
    if score is not None and score >= 80:
        return "Kuuma", "hot"
    return "Lämmin", "warm"


def _attach_ai_recommendations(leads: list[Lead], organization_id: int, now: datetime) -> None:
    if not leads:
        return
    lead_ids = [lead.id for lead in leads]
    seven_days_ago = now - timedelta(days=7)

    proposal_rows = (
        db.session.query(Proposal.lead_id, db.func.count(Proposal.id))
        .filter(
            Proposal.organization_id == organization_id,
            Proposal.lead_id.in_(lead_ids),
        )
        .group_by(Proposal.lead_id)
        .all()
    )
    proposals_count_by_lead = {int(lid): int(cnt) for lid, cnt in proposal_rows if lid}

    old_unviewed_rows = (
        db.session.query(Proposal.lead_id)
        .filter(
            Proposal.organization_id == organization_id,
            Proposal.lead_id.in_(lead_ids),
            Proposal.status == "sent",
            Proposal.sent_at.isnot(None),
            Proposal.sent_at <= seven_days_ago,
        )
        .distinct()
        .all()
    )
    old_unviewed_lead_ids = {int(lid) for (lid,) in old_unviewed_rows if lid}

    heavily_viewed_rows = (
        db.session.query(Proposal.lead_id)
        .filter(
            Proposal.organization_id == organization_id,
            Proposal.lead_id.in_(lead_ids),
            Proposal.opened_count >= 3,
        )
        .distinct()
        .all()
    )
    heavily_viewed_lead_ids = {int(lid) for (lid,) in heavily_viewed_rows if lid}

    for lead in leads:
        lead.ai_recommendation = LeadService._pipeline_ai_recommendation(
            lead,
            now=now,
            has_any_proposal=proposals_count_by_lead.get(lead.id, 0) > 0,
            has_old_unviewed_proposal=lead.id in old_unviewed_lead_ids,
            has_heavily_viewed_proposal=lead.id in heavily_viewed_lead_ids,
        )


def _format_recommendation(lead: Lead, text: str, organization_id: int, now: datetime) -> str:
    if text == "Ota yhteyttä nyt":
        contact_days = _days_since_contact(lead, organization_id, now)
        if contact_days is not None and contact_days > 0:
            return f"Ota yhteyttä — {contact_days} pv hiljaa"
    return text


def _recommendation_type(text: str) -> str:
    if text in RISK_RECOMMENDATIONS or "hiljaa" in text.lower():
        return "risk"
    return "opportunity"


class DashboardTodayService:
    @staticmethod
    def get_today_data(organization_id: int) -> dict:
        now = _utc_now()
        hot_leads = DashboardTodayService._get_hot_leads(organization_id, now)
        overdue_tasks = DashboardTodayService._get_overdue_tasks(organization_id, now)
        unprocessed_leads = DashboardTodayService._get_unprocessed_leads(organization_id, now)
        ai_recommendations = DashboardTodayService._get_ai_recommendations(organization_id, now)
        return {
            "hot_leads": hot_leads,
            "overdue_tasks": overdue_tasks,
            "unprocessed_leads": unprocessed_leads,
            "ai_recommendations": ai_recommendations,
        }

    @staticmethod
    def _get_hot_leads(organization_id: int, now: datetime) -> list[dict]:
        rows = (
            Lead.query.options(joinedload(Lead.stage))
            .filter(
                *_active_lead_filter(organization_id),
                Lead.score.isnot(None),
                Lead.score >= 70,
                _exclude_closed_stages(),
            )
            .order_by(Lead.score.desc())
            .limit(3)
            .all()
        )
        result = []
        for lead in rows:
            contact_days = _days_since_contact(lead, organization_id, now)
            result.append(
                {
                    "id": lead.id,
                    "name": lead.display_name,
                    "company": lead.company or "",
                    "score": lead.score,
                    "score_label": _score_badge_label(lead.score)[0],
                    "score_tier": _score_badge_label(lead.score)[1],
                    "days_since_contact": contact_days,
                    "url": f"/leads/{lead.id}",
                }
            )
        return result

    @staticmethod
    def _get_overdue_tasks(organization_id: int, now: datetime) -> list[dict]:
        rows = (
            Task.query.options(joinedload(Task.lead))
            .filter(
                Task.organization_id == organization_id,
                Task.status.in_(("pending", "in_progress")),
                Task.due_date < now,
            )
            .order_by(Task.due_date.asc())
            .limit(5)
            .all()
        )
        result = []
        for task in rows:
            due = _ensure_aware(task.due_date)
            days_overdue = days_since(due, now=now) if due else 0
            lead = task.lead
            result.append(
                {
                    "id": task.id,
                    "lead_name": lead.display_name if lead else "—",
                    "title": task.title,
                    "days_overdue": days_overdue or 0,
                    "lead_url": f"/leads/{lead.id}" if lead else None,
                    "complete_url": f"/tasks/{task.id}/complete",
                }
            )
        return result

    @staticmethod
    def _get_unprocessed_leads(organization_id: int, now: datetime) -> list[dict]:
        default_stage = get_default_stage(organization_id)
        seven_days_ago = now - timedelta(days=7)
        has_activity = exists().where(
            Activity.lead_id == Lead.id,
            Activity.organization_id == organization_id,
        )

        rows = (
            Lead.query.filter(
                Lead.organization_id == organization_id,
                Lead.status == "active",
                Lead.stage_id == default_stage.id,
                Lead.created_at >= seven_days_ago,
                not_(has_activity),
            )
            .order_by(Lead.created_at.desc())
            .limit(5)
            .all()
        )
        return [
            {
                "id": lead.id,
                "name": lead.display_name,
                "company": lead.company or "",
                "created_at_relative": time_ago_fi(lead.created_at, now=now),
                "source": lead.source or "",
                "url": f"/leads/{lead.id}",
            }
            for lead in rows
        ]

    @staticmethod
    def _get_ai_recommendations(organization_id: int, now: datetime) -> list[dict]:
        rows = (
            Lead.query.options(joinedload(Lead.stage))
            .filter(
                *_active_lead_filter(organization_id),
                _exclude_closed_stages(),
            )
            .order_by(Lead.score.desc().nullslast())
            .limit(50)
            .all()
        )
        _attach_ai_recommendations(rows, organization_id, now)

        result = []
        for lead in rows:
            rec = getattr(lead, "ai_recommendation", None)
            if not rec:
                continue
            formatted = _format_recommendation(lead, rec, organization_id, now)
            result.append(
                {
                    "id": lead.id,
                    "name": lead.display_name,
                    "company": lead.company or "",
                    "recommendation": formatted,
                    "type": _recommendation_type(formatted),
                    "url": f"/leads/{lead.id}",
                }
            )
            if len(result) >= 4:
                break
        return result

    @staticmethod
    def get_ai_worklist(organization_id: int) -> list[dict]:
        now = _utc_now()
        today = DashboardTodayService.get_today_data(organization_id)

        ranked: list[tuple[int, dict]] = []

        for task in today["overdue_tasks"]:
            if task["days_overdue"] > 2:
                ranked.append(
                    (
                        1,
                        {
                            "suggestion": (
                                f"Tee tehtävä: {task['title']} — "
                                f"{task['days_overdue']} pv myöhässä. "
                                f"Liidi: {task['lead_name']}"
                            ),
                            "url": task["lead_url"] or f"/tasks",
                            "kind": "overdue_task",
                        },
                    )
                )

        for lead in today["hot_leads"]:
            if lead["score"] is not None and lead["score"] >= 80:
                contact_days = lead.get("days_since_contact")
                if contact_days is not None and contact_days >= 7:
                    ranked.append(
                        (
                            2,
                            {
                                "suggestion": (
                                    f"Ota yhteyttä {lead['name']} ({lead['company']}) — "
                                    f"kuuma liidi, ei kontaktia {contact_days} pv"
                                ),
                                "url": lead["url"],
                                "kind": "hot_lead",
                            },
                        )
                    )

        for lead in today["unprocessed_leads"]:
            created_at = (
                Lead.query.filter_by(id=lead["id"], organization_id=organization_id)
                .with_entities(Lead.created_at)
                .scalar()
            )
            if created_at:
                created_at = _ensure_aware(created_at)
                if (now - created_at).total_seconds() < 86400:
                    ranked.append(
                        (
                            3,
                            {
                                "suggestion": (
                                    f"Käy läpi uusi liidi: {lead['name']} ({lead['company']}) — "
                                    f"saapui {lead['created_at_relative']}"
                                ),
                                "url": lead["url"],
                                "kind": "unprocessed_lead",
                            },
                        )
                    )

        warm_leads = (
            Lead.query.filter(
                *_active_lead_filter(organization_id),
                Lead.score.isnot(None),
                Lead.score >= 60,
                Lead.score <= 79,
                _exclude_closed_stages(),
            )
            .all()
        )
        for lead in warm_leads:
            contact_days = _days_since_contact(lead, organization_id, now)
            if contact_days is not None and contact_days >= 14:
                ranked.append(
                    (
                        4,
                        {
                            "suggestion": (
                                f"Ota yhteyttä {lead.display_name} ({lead.company or ''}) — "
                                f"lämmin liidi, ei kontaktia {contact_days} pv"
                            ),
                            "url": f"/leads/{lead.id}",
                            "kind": "warm_lead",
                        },
                    )
                )

        for rec in today["ai_recommendations"]:
            if rec["type"] == "risk":
                ranked.append(
                    (
                        5,
                        {
                            "suggestion": (
                                f"{rec['recommendation']} — {rec['name']} ({rec['company']})"
                            ),
                            "url": rec["url"],
                            "kind": "ai_risk",
                        },
                    )
                )

        ranked.sort(key=lambda item: item[0])
        return [item[1] for item in ranked[:5]]
