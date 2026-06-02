import csv
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.core.audit import log_audit
from app.core.security import normalize_email
from app.extensions import db
from app.leads.models import (
    DEFAULT_PIPELINE_STAGES,
    ACTIVITY_TYPES,
    Activity,
    Lead,
    PipelineStage,
)
from app.leads.validators import (
    has_useful_identifier,
    normalize_lead_data,
    normalize_tags,
    sanitize_csv_value,
    validate_lead_fields,
    validate_lead_source,
)
from app.users.models import User

LEAD_SORT_COLUMNS = {
    "name": "name",
    "company": "company",
    "stage": "stage_id",
    "score": "score",
    "deal_value": "deal_value",
    "source": "source",
    "created_at": "created_at",
    "last_activity": "last_activity_at",
}
LAST_ACTIVITY_TYPES = (
    "email",
    "email_sent",
    "call",
    "note",
    "meeting",
    "meeting_scheduled",
    "stage_change",
    "stage_changed",
    "ai_score",
    "ai_enriched",
)


def _safe_dispatch_webhook(event_type: str, payload: dict, organization_id: int, triggered_by=None) -> None:
    try:
        from app.webhooks.services import WebhookService

        WebhookService.dispatch(
            event_type,
            payload,
            organization_id,
            triggered_by=triggered_by,
        )
    except Exception:
        # Webhook failures must never break CRM actions.
        pass


class LeadServiceError(Exception):
    def __init__(self, message: str, code: str = "lead_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def seed_default_pipeline_stages(organization_id: int) -> list[PipelineStage]:
    existing = {
        s.name: s
        for s in PipelineStage.query.filter_by(organization_id=organization_id).all()
    }
    created = []
    for name, order_index, color in DEFAULT_PIPELINE_STAGES:
        if name in existing:
            continue
        stage = PipelineStage(
            organization_id=organization_id,
            name=name,
            order_index=order_index,
            color=color,
            is_default=True,
        )
        db.session.add(stage)
        created.append(stage)
    if created:
        db.session.flush()
    return created


def get_default_stage(organization_id: int) -> PipelineStage:
    stage = (
        PipelineStage.query.filter_by(organization_id=organization_id)
        .order_by(PipelineStage.order_index.asc())
        .first()
    )
    if not stage:
        seed_default_pipeline_stages(organization_id)
        db.session.flush()
        stage = (
            PipelineStage.query.filter_by(organization_id=organization_id)
            .order_by(PipelineStage.order_index.asc())
            .first()
        )
    if not stage:
        raise LeadServiceError("No pipeline stages available.", "no_stages")
    return stage


def _get_stage_for_org(stage_id: int, organization_id: int) -> PipelineStage:
    stage = PipelineStage.query.filter_by(id=stage_id, organization_id=organization_id).first()
    if not stage:
        raise LeadServiceError("Pipeline stage not found.", "invalid_stage")
    return stage


def get_lead_for_org(lead_id: int, organization_id: int) -> Lead:
    lead = (
        Lead.query.filter_by(id=lead_id, organization_id=organization_id)
        .options(joinedload(Lead.stage), joinedload(Lead.assignee))
        .first()
    )
    if not lead:
        raise LeadServiceError("Lead not found.", "not_found")
    return lead


def _validate_assignee(assigned_to: int | None, organization_id: int) -> None:
    if assigned_to is None:
        return
    user = User.query.filter_by(id=assigned_to, organization_id=organization_id).first()
    if not user:
        raise LeadServiceError("Assigned user not found in organization.", "invalid_assignee")


def _validate_assignment_permission(
    assigned_to: int | None,
    organization_id: int,
    *,
    actor_id: int | None = None,
    actor_role: str | None = None,
) -> None:
    _validate_assignee(assigned_to, organization_id)
    if assigned_to is None:
        return
    if actor_role in ("admin", "superadmin"):
        return
    if actor_role == "user" and assigned_to != actor_id:
        raise LeadServiceError(
            "You cannot assign leads to other users.", "forbidden_assign"
        )


def _status_from_stage_name(stage_name: str) -> str:
    lower = stage_name.strip().lower()
    if lower in {"won", "voitettu"}:
        return "won"
    if lower in {"lost", "hävitty"}:
        return "lost"
    if lower == "closed won":
        return "won"
    if lower == "closed lost":
        return "lost"
    return "active"


def _is_closed_lost_stage_name(stage_name: str | None) -> bool:
    return (stage_name or "").strip().lower() in {"lost", "closed lost", "hävitty"}


def _activity_change_value(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def _check_duplicate_source(organization_id: int, source: str, source_ref: str | None, exclude_id: int | None = None):
    if not source_ref or not str(source_ref).strip():
        return
    q = Lead.query.filter_by(
        organization_id=organization_id,
        source=source,
        source_ref=str(source_ref).strip(),
    )
    if exclude_id:
        q = q.filter(Lead.id != exclude_id)
    if q.first():
        raise LeadServiceError("A lead with this source and reference already exists.", "duplicate_source")


class LeadService:
    @staticmethod
    def _pipeline_ai_recommendation(
        lead: Lead,
        *,
        now: datetime,
        has_any_proposal: bool,
        has_old_unviewed_proposal: bool,
        has_heavily_viewed_proposal: bool,
    ) -> str | None:
        if lead.last_contacted_at:
            last_contacted = lead.last_contacted_at
            if last_contacted.tzinfo is None:
                last_contacted = last_contacted.replace(tzinfo=timezone.utc)
            if (now - last_contacted) > timedelta(days=14):
                return "Ota yhteyttä nyt"
        if (lead.score or 0) > 80 and not has_any_proposal:
            return "Lähetä tarjous"
        if has_old_unviewed_proposal:
            return "Muistuta tarjouksesta"
        if has_heavily_viewed_proposal:
            return "Seuraa välittömästi"
        return None

    @staticmethod
    def log_activity(
        lead_id: int,
        user_id: int | None,
        activity_type: str,
        *,
        content: str | None = None,
        metadata: dict | None = None,
    ) -> Activity:
        if activity_type not in ACTIVITY_TYPES:
            raise LeadServiceError("Invalid activity type.", "invalid_activity")
        lead = db.session.get(Lead, lead_id)
        if not lead:
            raise LeadServiceError("Lead not found.", "not_found")
        activity = Activity(
            lead_id=lead.id,
            user_id=user_id,
            organization_id=lead.organization_id,
            type=activity_type,
            content=content,
            metadata_json=metadata,
        )
        db.session.add(activity)
        return activity

    @staticmethod
    def create(
        data: dict,
        organization_id: int,
        user_id: int | None = None,
        *,
        actor_role: str | None = None,
    ) -> Lead:
        data = normalize_lead_data(data)
        ok, msg = validate_lead_fields(data, require_identifier=True)
        if not ok:
            raise LeadServiceError(msg, "validation_error")

        if not validate_lead_source(data.get("source", "manual")):
            data["source"] = "manual"

        assigned_to = data.get("assigned_to")
        _validate_assignment_permission(
            assigned_to,
            organization_id,
            actor_id=user_id,
            actor_role=actor_role,
        )

        stage_id = data.get("stage_id")
        if stage_id:
            stage = _get_stage_for_org(stage_id, organization_id)
        else:
            stage = get_default_stage(organization_id)

        _check_duplicate_source(organization_id, data.get("source", "manual"), data.get("source_ref"))

        lead = Lead(
            organization_id=organization_id,
            assigned_to=assigned_to,
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            email=data.get("email"),
            phone=data.get("phone"),
            company=data.get("company"),
            title=data.get("title"),
            website=data.get("website"),
            linkedin_url=data.get("linkedin_url"),
            stage_id=stage.id,
            status=_status_from_stage_name(stage.name) if stage.name.lower() in ("won", "lost", "voitettu", "hävitty") else "active",
            source=data.get("source", "manual"),
            source_ref=data.get("source_ref"),
            score=data.get("score") if data.get("score") is not None and data.get("score") != "" else None,
            deal_value=(
                Decimal(str(data["deal_value"]))
                if data.get("deal_value") not in (None, "")
                else None
            ),
            score_reason=data.get("score_reason"),
            notes=data.get("notes"),
            tags=normalize_tags(data.get("tags", [])),
            ai_enriched=data.get("ai_enriched", False),
            ai_summary=data.get("ai_summary"),
            ai_company_info=data.get("ai_company_info"),
            ai_contact_info=data.get("ai_contact_info"),
        )
        db.session.add(lead)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            raise LeadServiceError("A lead with this source and reference already exists.", "duplicate_source") from None

        LeadService.log_activity(lead.id, user_id, "created")
        log_audit(
            "lead_created",
            user_id=user_id,
            organization_id=organization_id,
            target_type="lead",
            target_id=lead.id,
        )
        from app.ai.triggers import apply_enrichment_on_create

        apply_enrichment_on_create(lead)

        from app.tasks.services import TaskService

        try:
            TaskService.create_auto_tasks(lead, "new_lead")
        except Exception:
            pass

        try:
            from app.sequences.services import SequenceService

            SequenceService.trigger_auto_enroll(lead, "on_lead_created")
        except Exception:
            pass

        from app.automations.triggers import fire_automation_trigger

        fire_automation_trigger(
            "lead_created",
            {"lead_id": lead.id},
            organization_id,
        )
        _safe_dispatch_webhook(
            "lead.created",
            {
                "lead": {
                    "id": lead.id,
                    "name": lead.display_name,
                    "company": lead.company,
                    "score": lead.score,
                    "source": lead.source,
                    "crm_url": f"https://app.flowleads.fi/leads/{lead.id}",
                }
            },
            organization_id,
            triggered_by={"id": user_id} if user_id else "system",
        )
        if lead.assigned_to:
            _safe_dispatch_webhook(
                "lead.assigned",
                {"lead": {"id": lead.id, "assigned_to": lead.assigned_to}},
                organization_id,
                triggered_by={"id": user_id} if user_id else "system",
            )

        return lead

    @staticmethod
    def update(
        lead_id: int,
        data: dict,
        organization_id: int,
        user_id: int | None = None,
        *,
        actor_role: str | None = None,
    ) -> Lead:
        lead = get_lead_for_org(lead_id, organization_id)
        previous_score = lead.score
        data = normalize_lead_data(data)

        if lead.status == "archived":
            raise LeadServiceError("Cannot update archived lead.", "archived")

        ok, msg = validate_lead_fields(data, require_identifier=False)
        if not ok:
            raise LeadServiceError(msg, "validation_error")

        merged = {
            "email": data.get("email", lead.email),
            "phone": data.get("phone", lead.phone),
            "company": data.get("company", lead.company),
            "first_name": data.get("first_name", lead.first_name),
            "last_name": data.get("last_name", lead.last_name),
            "source_ref": data.get("source_ref", lead.source_ref),
        }
        if not has_useful_identifier(merged):
            raise LeadServiceError(
                "At least one identifier is required.", "validation_error"
            )

        changes = {}
        updatable = (
            "first_name", "last_name", "email", "phone", "company", "title",
            "website", "linkedin_url", "notes", "score", "score_reason",
            "source", "source_ref", "assigned_to", "tags", "deal_value",
        )
        for field in updatable:
            if field in data:
                old = getattr(lead, field)
                new = data[field]
                if field == "tags":
                    new = normalize_tags(new)
                    old_tags = set(old or [])
                    new_tags = set(new or [])
                    added_tags = list(new_tags - old_tags)
                    if added_tags:
                        from app.automations.triggers import fire_automation_trigger

                        for tag in added_tags:
                            fire_automation_trigger(
                                "lead_tag_added",
                                {"lead_id": lead.id, "added_tags": [tag], "tag": tag},
                                organization_id,
                            )
                if field == "score" and old != new:
                    from app.automations.triggers import fire_automation_trigger

                    fire_automation_trigger(
                        "lead_score_changed",
                        {
                            "lead_id": lead.id,
                            "old_score": old,
                            "new_score": new,
                        },
                        organization_id,
                    )
                if field == "assigned_to":
                    _validate_assignment_permission(
                        new,
                        organization_id,
                        actor_id=user_id,
                        actor_role=actor_role,
                    )
                if field == "source" and new:
                    if not validate_lead_source(new):
                        raise LeadServiceError("Invalid lead source.", "validation_error")
                if field == "deal_value":
                    if new is None or new == "":
                        new = None
                    else:
                        new = Decimal(str(new))
                if old != new:
                    changes[field] = {
                        "old": _activity_change_value(old),
                        "new": _activity_change_value(new),
                    }
                    setattr(lead, field, new)

        if lead.deal_value is not None and lead.close_probability is not None:
            lead.expected_value = Decimal(
                str(round(float(lead.deal_value) * float(lead.close_probability), 2))
            )
        elif "deal_value" in data and lead.deal_value is None:
            lead.expected_value = None

        if "stage_id" in data and data["stage_id"]:
            new_stage = _get_stage_for_org(data["stage_id"], organization_id)
            if new_stage.id != lead.stage_id:
                old_stage = lead.stage
                lead.stage_id = new_stage.id
                lead.status = _status_from_stage_name(new_stage.name)
                LeadService.log_activity(
                    lead.id,
                    user_id,
                    "stage_changed",
                    metadata={
                        "old_stage_id": old_stage.id,
                        "new_stage_id": new_stage.id,
                        "old_stage_name": old_stage.name,
                        "new_stage_name": new_stage.name,
                    },
                )
                from app.tasks.services import TaskService

                try:
                    TaskService.create_auto_tasks(lead, "stage_change")
                except Exception:
                    pass

                try:
                    from app.sequences.services import SequenceService

                    SequenceService.trigger_auto_enroll(
                        lead,
                        "on_stage_change",
                        payload={"stage_id": new_stage.id},
                    )
                except Exception:
                    pass

                from app.automations.triggers import fire_automation_trigger

                fire_automation_trigger(
                    "lead_stage_changed",
                    {
                        "lead_id": lead.id,
                        "old_stage_id": old_stage.id,
                        "new_stage_id": new_stage.id,
                    },
                    organization_id,
                )
                _safe_dispatch_webhook(
                    "lead.stage_changed",
                    {
                        "lead": {
                            "id": lead.id,
                            "name": lead.display_name,
                            "old_stage_id": old_stage.id,
                            "new_stage_id": new_stage.id,
                        }
                    },
                    organization_id,
                    triggered_by={"id": user_id} if user_id else "system",
                )

        if "source" in data or "source_ref" in data:
            _check_duplicate_source(
                organization_id, lead.source, lead.source_ref, exclude_id=lead.id
            )

        lead.updated_at = datetime.now(timezone.utc)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            raise LeadServiceError("A lead with this source and reference already exists.", "duplicate_source") from None

        if changes:
            if "assigned_to" in changes and lead.assigned_to:
                _safe_dispatch_webhook(
                    "lead.assigned",
                    {"lead": {"id": lead.id, "assigned_to": lead.assigned_to}},
                    organization_id,
                    triggered_by={"id": user_id} if user_id else "system",
                )
            if "score" in changes:
                old_score = previous_score if previous_score is not None else 0
                new_score = lead.score if lead.score is not None else 0
                if abs(int(new_score) - int(old_score)) > 10:
                    _safe_dispatch_webhook(
                        "lead.score_updated",
                        {
                            "lead": {
                                "id": lead.id,
                                "name": lead.display_name,
                                "old_score": previous_score,
                                "new_score": lead.score,
                            }
                        },
                        organization_id,
                        triggered_by={"id": user_id} if user_id else "system",
                    )
                if int(old_score) < 80 <= int(new_score):
                    _safe_dispatch_webhook(
                        "lead.high_score",
                        {
                            "lead": {
                                "id": lead.id,
                                "name": lead.display_name,
                                "old_score": previous_score,
                                "new_score": lead.score,
                            }
                        },
                        organization_id,
                        triggered_by={"id": user_id} if user_id else "system",
                    )
            LeadService.log_activity(lead.id, user_id, "updated", metadata={"changes": changes})
            log_audit(
                "lead_updated",
                user_id=user_id,
                organization_id=organization_id,
                target_type="lead",
                target_id=lead.id,
                metadata={"fields": list(changes.keys())},
            )
            from app.ai.triggers import apply_enrichment_on_update

            apply_enrichment_on_update(lead, set(changes.keys()))
        return lead

    @staticmethod
    def move_stage(
        lead_id: int,
        stage_id: int,
        organization_id: int,
        user_id: int | None,
        *,
        lost_reason: str | None = None,
        lost_reason_note: str | None = None,
    ) -> Lead:
        lead = get_lead_for_org(lead_id, organization_id)
        if lead.status == "archived":
            raise LeadServiceError("Cannot move archived lead.", "archived")

        new_stage = _get_stage_for_org(stage_id, organization_id)
        if lead.stage_id == new_stage.id:
            return lead

        old_stage = lead.stage
        lead.stage_id = new_stage.id
        lead.status = _status_from_stage_name(new_stage.name)
        lead.updated_at = datetime.now(timezone.utc)
        if _is_closed_lost_stage_name(new_stage.name):
            if not (lost_reason or "").strip():
                raise LeadServiceError(
                    "lost_reason on pakollinen siirrettäessä hävittyyn vaiheeseen.",
                    "validation_error",
                )
            lead.lost_reason = (lost_reason or "").strip()[:100]
            lead.lost_reason_note = (lost_reason_note or "").strip() or None
        elif lead.status != "lost":
            lead.lost_reason = None
            lead.lost_reason_note = None

        LeadService.log_activity(
            lead.id,
            user_id,
            "stage_changed",
            content=(
                f"Liidi hävisi: {(lost_reason or '').strip()[:100]}"
                if _is_closed_lost_stage_name(new_stage.name)
                else None
            ),
            metadata={
                "old_stage_id": old_stage.id,
                "new_stage_id": new_stage.id,
                "old_stage_name": old_stage.name,
                "new_stage_name": new_stage.name,
                "lost_reason": lead.lost_reason if _is_closed_lost_stage_name(new_stage.name) else None,
            },
        )
        if _is_closed_lost_stage_name(new_stage.name):
            log_audit(
                "lead_lost",
                user_id=user_id,
                organization_id=organization_id,
                target_type="lead",
                target_id=lead.id,
                metadata={"reason": lead.lost_reason},
            )

        db.session.flush()

        from app.tasks.services import TaskService

        try:
            TaskService.create_auto_tasks(lead, "stage_change")
        except Exception:
            pass

        try:
            from app.sequences.services import SequenceService

            SequenceService.trigger_auto_enroll(
                lead,
                "on_stage_change",
                payload={"stage_id": new_stage.id},
            )
        except Exception:
            pass

        from app.automations.triggers import fire_automation_trigger

        fire_automation_trigger(
            "lead_stage_changed",
            {
                "lead_id": lead.id,
                "old_stage_id": old_stage.id,
                "new_stage_id": new_stage.id,
            },
            organization_id,
        )
        _safe_dispatch_webhook(
            "lead.stage_changed",
            {
                "lead": {
                    "id": lead.id,
                    "name": lead.display_name,
                    "old_stage_id": old_stage.id,
                    "new_stage_id": new_stage.id,
                }
            },
            organization_id,
            triggered_by={"id": user_id} if user_id else "system",
        )

        return lead

    @staticmethod
    def archive(lead_id: int, organization_id: int, user_id: int | None) -> Lead:
        lead = get_lead_for_org(lead_id, organization_id)
        if lead.status == "archived":
            return lead
        lead.status = "archived"
        lead.updated_at = datetime.now(timezone.utc)
        LeadService.log_activity(lead.id, user_id, "archived")
        log_audit(
            "lead_archived",
            user_id=user_id,
            organization_id=organization_id,
            target_type="lead",
            target_id=lead.id,
        )
        db.session.flush()
        return lead

    @staticmethod
    def add_note(lead_id: int, content: str, organization_id: int, user_id: int | None) -> Activity:
        lead = get_lead_for_org(lead_id, organization_id)
        if not content or not content.strip():
            raise LeadServiceError("Note content is required.", "validation_error")
        if len(content) > 5000:
            raise LeadServiceError("Note must be at most 5000 characters.", "validation_error")
        return LeadService.log_activity(lead.id, user_id, "note", content=content.strip())

    @staticmethod
    def _apply_filters(query, organization_id: int, filters: dict | None, latest_activity=None):
        filters = filters or {}
        query = query.filter(Lead.organization_id == organization_id)

        status = filters.get("status")
        if status == "archived":
            query = query.filter(Lead.status == "archived")
        elif status:
            query = query.filter(Lead.status == status)
        else:
            query = query.filter(Lead.status != "archived")

        if filters.get("stage_id"):
            query = query.filter(Lead.stage_id == int(filters["stage_id"]))
        if filters.get("source"):
            query = query.filter(Lead.source == filters["source"])
        if filters.get("assigned_to"):
            query = query.filter(Lead.assigned_to == int(filters["assigned_to"]))
        if filters.get("score_min") is not None and filters.get("score_min") != "":
            query = query.filter(Lead.score >= int(filters["score_min"]))
        if filters.get("score_max") is not None and filters.get("score_max") != "":
            query = query.filter(Lead.score <= int(filters["score_max"]))
        if filters.get("created_from"):
            query = query.filter(Lead.created_at >= filters["created_from"])
        if filters.get("created_to"):
            query = query.filter(Lead.created_at <= filters["created_to"])
        if filters.get("gdpr_consent"):
            query = query.filter(Lead.gdpr_consent.is_(True))
        if filters.get("marketing_opt_in"):
            query = query.filter(Lead.marketing_opt_in.is_(True))
        if filters.get("unsubscribed"):
            query = query.filter(Lead.unsubscribed.is_(True))
        if filters.get("is_anonymized"):
            query = query.filter(Lead.is_anonymized.is_(True))
        if filters.get("no_contact_7"):
            if latest_activity is None:
                seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
                latest_activity_max = (
                    db.session.query(
                        Activity.lead_id.label("lead_id"),
                        func.max(Activity.created_at).label("last_activity_at"),
                    )
                    .filter(
                        Activity.organization_id == organization_id,
                        Activity.type.in_(LAST_ACTIVITY_TYPES),
                    )
                    .group_by(Activity.lead_id)
                    .subquery()
                )
                query = query.outerjoin(latest_activity_max, Lead.id == latest_activity_max.c.lead_id)
                query = query.filter(
                    or_(
                        latest_activity_max.c.last_activity_at.is_(None),
                        latest_activity_max.c.last_activity_at < seven_days_ago,
                    )
                )
            else:
                seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
                query = query.filter(
                    or_(
                        latest_activity.c.last_activity_at.is_(None),
                        latest_activity.c.last_activity_at < seven_days_ago,
                    )
                )

        search = (filters.get("search") or "").strip()
        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Lead.first_name.ilike(pattern),
                    Lead.last_name.ilike(pattern),
                    Lead.email.ilike(pattern),
                    Lead.company.ilike(pattern),
                )
            )
        return query

    @staticmethod
    def search(organization_id: int, filters: dict | None, page: int = 1, per_page: int = 25):
        page = max(1, int(page))
        per_page = max(1, min(100, int(per_page)))

        ranked_activities = (
            db.session.query(
                Activity.lead_id.label("lead_id"),
                Activity.created_at.label("last_activity_at"),
                Activity.type.label("last_activity_type"),
                func.row_number()
                .over(partition_by=Activity.lead_id, order_by=Activity.created_at.desc())
                .label("rn"),
            )
            .filter(
                Activity.organization_id == organization_id,
                Activity.type.in_(LAST_ACTIVITY_TYPES),
            )
            .subquery()
        )
        latest_activity = (
            db.session.query(
                ranked_activities.c.lead_id,
                ranked_activities.c.last_activity_at,
                ranked_activities.c.last_activity_type,
            )
            .filter(ranked_activities.c.rn == 1)
            .subquery()
        )
        query = (
            Lead.query.options(joinedload(Lead.stage), joinedload(Lead.assignee))
            .outerjoin(latest_activity, Lead.id == latest_activity.c.lead_id)
            .add_columns(
                latest_activity.c.last_activity_at,
                latest_activity.c.last_activity_type,
            )
        )
        query = LeadService._apply_filters(query, organization_id, filters, latest_activity)

        sort_col = filters.get("sort") if filters else None
        sort_dir = (filters.get("dir") or "desc").lower() if filters else "desc"
        if sort_col not in LEAD_SORT_COLUMNS:
            sort_col = "created_at"
        if sort_dir not in ("asc", "desc"):
            sort_dir = "desc"

        if sort_col == "name":
            if sort_dir == "asc":
                query = query.order_by(Lead.first_name.asc(), Lead.last_name.asc())
            else:
                query = query.order_by(Lead.first_name.desc(), Lead.last_name.desc())
        elif sort_col == "last_activity":
            col = latest_activity.c.last_activity_at
            if sort_dir == "asc":
                query = query.order_by(col.asc().nullsfirst(), Lead.created_at.desc())
            else:
                query = query.order_by(col.desc().nullslast(), Lead.created_at.desc())
        else:
            col = getattr(Lead, LEAD_SORT_COLUMNS[sort_col])
            query = query.order_by(col.asc() if sort_dir == "asc" else col.desc())

        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        hydrated_items = []
        for item in pagination.items:
            if isinstance(item, Lead):
                lead = item
                last_activity_at = None
                last_activity_type = None
            else:
                lead, last_activity_at, last_activity_type = item
            if last_activity_at and getattr(last_activity_at, "tzinfo", None) is None:
                last_activity_at = last_activity_at.replace(tzinfo=timezone.utc)
            lead.last_activity_at = last_activity_at
            lead.last_activity_type = last_activity_type
            hydrated_items.append(lead)
        pagination.items = hydrated_items
        return pagination

    @staticmethod
    def get_pipeline_data(organization_id: int, filters: dict | None = None) -> dict:
        filters = filters or {}
        stages = (
            PipelineStage.query.filter_by(organization_id=organization_id)
            .order_by(PipelineStage.order_index.asc())
            .all()
        )
        query = Lead.query.options(joinedload(Lead.assignee)).filter(
            Lead.organization_id == organization_id,
            Lead.status != "archived",
        )
        if filters.get("assigned_to"):
            query = query.filter(Lead.assigned_to == int(filters["assigned_to"]))
        if filters.get("source"):
            query = query.filter(Lead.source == filters["source"])
        if filters.get("score_min") is not None and filters.get("score_min") != "":
            query = query.filter(Lead.score >= int(filters["score_min"]))
        if filters.get("score_max") is not None and filters.get("score_max") != "":
            query = query.filter(Lead.score <= int(filters["score_max"]))
        if filters.get("created_from"):
            query = query.filter(Lead.created_at >= filters["created_from"])
        if filters.get("created_to"):
            query = query.filter(Lead.created_at <= filters["created_to"])

        leads = query.all()
        lead_ids = [lead.id for lead in leads]

        open_tasks_by_lead: dict[int, int] = {}
        next_task_by_lead: dict[int, object] = {}
        last_activity_by_lead: dict[int, Activity] = {}
        stage_entered_at_by_lead: dict[int, datetime] = {}
        sequence_active_by_lead: dict[int, bool] = {}
        proposals_count_by_lead: dict[int, int] = {}
        old_unviewed_proposal_lead_ids: set[int] = set()
        heavily_viewed_proposal_lead_ids: set[int] = set()
        now = datetime.now(timezone.utc)
        seven_days_ago = now - timedelta(days=7)

        if lead_ids:
            from app.tasks.models import Task
            from app.sequences.models import EmailSequenceEnrollment
            from app.proposals.models import Proposal

            task_rows = (
                db.session.query(Task.lead_id, func.count(Task.id))
                .filter(
                    Task.organization_id == organization_id,
                    Task.lead_id.in_(lead_ids),
                    Task.status.in_(("pending", "in_progress")),
                )
                .group_by(Task.lead_id)
                .all()
            )
            open_tasks_by_lead = {int(lead_id): int(count) for lead_id, count in task_rows if lead_id}

            ranked_tasks = (
                db.session.query(
                    Task.id.label("id"),
                    Task.lead_id.label("lead_id"),
                    func.row_number()
                    .over(partition_by=Task.lead_id, order_by=Task.due_date.asc())
                    .label("rn"),
                )
                .filter(
                    Task.organization_id == organization_id,
                    Task.lead_id.in_(lead_ids),
                    Task.status.in_(("pending", "in_progress")),
                )
                .subquery()
            )
            next_tasks = (
                db.session.query(Task)
                .join(ranked_tasks, Task.id == ranked_tasks.c.id)
                .filter(ranked_tasks.c.rn == 1)
                .all()
            )
            next_task_by_lead = {task.lead_id: task for task in next_tasks if task.lead_id}

            ranked_activities = (
                db.session.query(
                    Activity.id.label("id"),
                    Activity.lead_id.label("lead_id"),
                    func.row_number()
                    .over(partition_by=Activity.lead_id, order_by=Activity.created_at.desc())
                    .label("rn"),
                )
                .filter(
                    Activity.organization_id == organization_id,
                    Activity.lead_id.in_(lead_ids),
                )
                .subquery()
            )
            latest_activities = (
                db.session.query(Activity)
                .join(ranked_activities, Activity.id == ranked_activities.c.id)
                .filter(ranked_activities.c.rn == 1)
                .all()
            )
            last_activity_by_lead = {activity.lead_id: activity for activity in latest_activities if activity.lead_id}

            stage_events = (
                Activity.query.filter(
                    Activity.organization_id == organization_id,
                    Activity.lead_id.in_(lead_ids),
                    Activity.type == "stage_changed",
                )
                .order_by(Activity.lead_id.asc(), Activity.created_at.desc())
                .all()
            )
            stage_by_lead_id = {lead.id: lead.stage_id for lead in leads}
            for activity in stage_events:
                current_stage_id = stage_by_lead_id.get(activity.lead_id)
                if not current_stage_id or activity.lead_id in stage_entered_at_by_lead:
                    continue
                metadata = activity.metadata_json or {}
                try:
                    new_stage_id = int(metadata.get("new_stage_id"))
                except (TypeError, ValueError):
                    continue
                if new_stage_id == current_stage_id:
                    stage_entered_at_by_lead[activity.lead_id] = activity.created_at

            sequence_rows = (
                db.session.query(EmailSequenceEnrollment.lead_id)
                .filter(
                    EmailSequenceEnrollment.organization_id == organization_id,
                    EmailSequenceEnrollment.lead_id.in_(lead_ids),
                    EmailSequenceEnrollment.status == "active",
                )
                .distinct()
                .all()
            )
            sequence_active_by_lead = {int(lead_id): True for (lead_id,) in sequence_rows if lead_id}

            proposal_rows = (
                db.session.query(Proposal.lead_id, func.count(Proposal.id))
                .filter(
                    Proposal.organization_id == organization_id,
                    Proposal.lead_id.in_(lead_ids),
                )
                .group_by(Proposal.lead_id)
                .all()
            )
            proposals_count_by_lead = {
                int(lead_id): int(count) for lead_id, count in proposal_rows if lead_id
            }

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
            old_unviewed_proposal_lead_ids = {
                int(lead_id) for (lead_id,) in old_unviewed_rows if lead_id
            }

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
            heavily_viewed_proposal_lead_ids = {
                int(lead_id) for (lead_id,) in heavily_viewed_rows if lead_id
            }

        def _to_utc(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        def recommendation_type(text: str | None) -> str:
            if not text:
                return "opportunity"
            if text in {"Muistuta tarjouksesta", "Seuraa välittömästi"}:
                return "followup"
            if text == "Ota yhteyttä nyt" or "hiljaa" in text.lower():
                return "risk"
            return "opportunity"

        by_stage = {s.id: [] for s in stages}
        stage_totals = {s.id: Decimal("0") for s in stages}
        for lead in leads:
            if lead.stage_id in by_stage:
                lead.open_tasks_count = open_tasks_by_lead.get(lead.id, 0)
                lead.sequence_active = sequence_active_by_lead.get(lead.id, False)
                rec_text = LeadService._pipeline_ai_recommendation(
                    lead,
                    now=now,
                    has_any_proposal=proposals_count_by_lead.get(lead.id, 0) > 0,
                    has_old_unviewed_proposal=lead.id in old_unviewed_proposal_lead_ids,
                    has_heavily_viewed_proposal=lead.id in heavily_viewed_proposal_lead_ids,
                )
                rec_type = recommendation_type(rec_text)
                lead.ai_recommendation = (
                    {"type": rec_type, "text": rec_text}
                    if rec_text
                    else None
                )
                last_activity = last_activity_by_lead.get(lead.id)
                last_activity_at = _to_utc(last_activity.created_at) if last_activity else None
                last_activity_days = (
                    (now - last_activity_at).days
                    if last_activity_at
                    else None
                )
                entered_at = _to_utc(
                    stage_entered_at_by_lead.get(lead.id) or lead.updated_at or lead.created_at
                )
                stage_days = (now - entered_at).days if entered_at else 0
                by_stage[lead.stage_id].append(
                    {
                        "lead": lead,
                        "last_activity": last_activity,
                        "last_activity_days": last_activity_days,
                        "stage_days": stage_days,
                        "next_task": next_task_by_lead.get(lead.id),
                        "ai_recommendation": lead.ai_recommendation,
                    }
                )
                if lead.deal_value is not None:
                    stage_totals[lead.stage_id] += lead.deal_value

        return {
            "stages": stages,
            "leads_by_stage": by_stage,
            "stage_deal_totals": {sid: float(stage_totals[sid]) for sid in stage_totals},
        }

    @staticmethod
    def export_csv(organization_id: int, filters: dict | None = None, selected_ids: list | None = None) -> str:
        query = Lead.query.options(joinedload(Lead.stage), joinedload(Lead.assignee))
        if selected_ids:
            query = query.filter(
                Lead.organization_id == organization_id,
                Lead.id.in_(selected_ids),
                Lead.status != "archived",
            )
        else:
            query = LeadService._apply_filters(query, organization_id, filters)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "First Name", "Last Name", "Email", "Phone", "Company", "Title",
            "Stage", "Status", "Score", "Source", "Assigned To", "Tags", "Created At",
        ])
        for lead in query.all():
            assignee_email = lead.assignee.email if lead.assignee else ""
            writer.writerow([
                lead.id,
                sanitize_csv_value(lead.first_name),
                sanitize_csv_value(lead.last_name),
                sanitize_csv_value(lead.email),
                sanitize_csv_value(lead.phone),
                sanitize_csv_value(lead.company),
                sanitize_csv_value(lead.title),
                sanitize_csv_value(lead.stage.name if lead.stage else ""),
                lead.status,
                lead.score if lead.score is not None else "",
                lead.source,
                sanitize_csv_value(assignee_email),
                sanitize_csv_value(", ".join(lead.tags or [])),
                lead.created_at.isoformat() if lead.created_at else "",
            ])
        return output.getvalue()

    @staticmethod
    def bulk_action(action: str, lead_ids: list[int], organization_id: int, user_id: int | None, payload: dict | None = None):
        if not lead_ids:
            raise LeadServiceError("No leads selected.", "validation_error")

        leads = Lead.query.filter(
            Lead.id.in_(lead_ids),
            Lead.organization_id == organization_id,
        ).all()
        if len(leads) != len(set(lead_ids)):
            raise LeadServiceError("One or more leads not found.", "not_found")

        payload = payload or {}

        if action == "assign":
            assigned_to = payload.get("assigned_to")
            _validate_assignee(assigned_to, organization_id)
            for lead in leads:
                if lead.assigned_to != assigned_to:
                    lead.assigned_to = assigned_to
                    LeadService.log_activity(
                        lead.id, user_id, "assigned",
                        metadata={"assigned_to": assigned_to},
                    )
                    if assigned_to:
                        _safe_dispatch_webhook(
                            "lead.assigned",
                            {"lead": {"id": lead.id, "assigned_to": assigned_to}},
                            organization_id,
                            triggered_by={"id": user_id} if user_id else "system",
                        )
            db.session.flush()
            return {"updated": len(leads)}

        if action == "change_stage":
            stage_id = payload.get("stage_id")
            if not stage_id:
                raise LeadServiceError("Stage is required.", "validation_error")
            for lead in leads:
                LeadService.move_stage(lead.id, int(stage_id), organization_id, user_id)
            return {"updated": len(leads)}

        if action == "archive":
            for lead in leads:
                LeadService.archive(lead.id, organization_id, user_id)
            return {"archived": len(leads)}

        if action == "export":
            csv_data = LeadService.export_csv(organization_id, selected_ids=lead_ids)
            return {"csv": csv_data}

        raise LeadServiceError("Invalid bulk action.", "invalid_action")
