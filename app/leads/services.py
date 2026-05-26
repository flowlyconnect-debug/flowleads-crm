import csv
import io
from datetime import datetime, timezone

from sqlalchemy import or_
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
    "source": "source",
    "created_at": "created_at",
}


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
    if lower == "won":
        return "won"
    if lower == "lost":
        return "lost"
    return "active"


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
            status=_status_from_stage_name(stage.name) if stage.name.lower() in ("won", "lost") else "active",
            source=data.get("source", "manual"),
            source_ref=data.get("source_ref"),
            score=data.get("score") if data.get("score") is not None and data.get("score") != "" else None,
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
            "source", "source_ref", "assigned_to", "tags",
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
                if old != new:
                    changes[field] = {"old": old, "new": new}
                    setattr(lead, field, new)

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
    def move_stage(lead_id: int, stage_id: int, organization_id: int, user_id: int | None) -> Lead:
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
    def _apply_filters(query, organization_id: int, filters: dict | None):
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

        query = Lead.query.options(joinedload(Lead.stage), joinedload(Lead.assignee))
        query = LeadService._apply_filters(query, organization_id, filters)

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
        else:
            col = getattr(Lead, LEAD_SORT_COLUMNS[sort_col])
            query = query.order_by(col.asc() if sort_dir == "asc" else col.desc())

        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
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
        by_stage = {s.id: [] for s in stages}
        for lead in leads:
            if lead.stage_id in by_stage:
                last_activity = (
                    Activity.query.filter_by(lead_id=lead.id, organization_id=organization_id)
                    .order_by(Activity.created_at.desc())
                    .first()
                )
                by_stage[lead.stage_id].append({"lead": lead, "last_activity": last_activity})

        return {"stages": stages, "leads_by_stage": by_stage}

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
