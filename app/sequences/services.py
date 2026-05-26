from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from flask import url_for
from sqlalchemy.orm import joinedload

from app.email.services import EmailService, EmailServiceError
from app.extensions import db
from app.leads.models import ACTIVITY_TYPES, Lead
from app.leads.services import LeadService, LeadServiceError, get_lead_for_org
from app.sequences.models import (
    ENROLLMENT_STATUSES,
    TRIGGER_TYPES,
    EmailSequence,
    EmailSequenceEnrollment,
    EmailSequenceSent,
    EmailSequenceStep,
)
from app.sequences.tokens import generate_unsubscribe_token
from app.segments.services import SegmentService

logger = logging.getLogger(__name__)

TEMPLATE_VAR_PATTERN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class SequenceServiceError(Exception):
    def __init__(self, message: str, code: str = "sequence_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_sequence_for_org(sequence_id: int, organization_id: int) -> EmailSequence:
    sequence = (
        EmailSequence.query.filter_by(id=sequence_id, organization_id=organization_id)
        .options(joinedload(EmailSequence.steps))
        .first()
    )
    if not sequence:
        raise SequenceServiceError("Sequence not found.", "not_found")
    return sequence


def get_enrollment_for_org(enrollment_id: int, organization_id: int) -> EmailSequenceEnrollment:
    enrollment = EmailSequenceEnrollment.query.filter_by(
        id=enrollment_id, organization_id=organization_id
    ).first()
    if not enrollment:
        raise SequenceServiceError("Enrollment not found.", "not_found")
    return enrollment


def _log_sequence_activity(
    lead_id: int,
    user_id: int | None,
    activity_type: str,
    *,
    content: str | None = None,
    metadata: dict | None = None,
) -> None:
    if activity_type not in ACTIVITY_TYPES:
        raise SequenceServiceError("Invalid activity type.", "invalid_activity")
    LeadService.log_activity(
        lead_id,
        user_id,
        activity_type,
        content=content,
        metadata=metadata,
    )


def _build_template_context(lead: Lead) -> dict[str, str]:
    first = (lead.first_name or "").strip()
    last = (lead.last_name or "").strip()
    name = " ".join(p for p in (first, last) if p).strip()
    return {
        "first_name": first,
        "last_name": last,
        "name": name or (lead.company or "") or (lead.email or ""),
        "company": (lead.company or "").strip(),
        "email": (lead.email or "").strip(),
        "phone": (lead.phone or "").strip(),
        "lead_id": str(lead.id),
    }


def render_sequence_template(text: str | None, lead: Lead) -> str:
    if not text:
        return ""
    context = _build_template_context(lead)

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        return context.get(key, "")

    return TEMPLATE_VAR_PATTERN.sub(replacer, text)


def _calculate_next_send_at(step: EmailSequenceStep, from_time: datetime | None = None) -> datetime:
    base = _ensure_tz(from_time or _utc_now())
    return base + timedelta(days=step.delay_days or 0, hours=step.delay_hours or 0)


def _ordered_steps(sequence: EmailSequence) -> list[EmailSequenceStep]:
    if sequence.steps:
        return sorted(sequence.steps, key=lambda s: s.order_index)
    return (
        EmailSequenceStep.query.filter_by(sequence_id=sequence.id)
        .order_by(EmailSequenceStep.order_index.asc())
        .all()
    )


def _get_step_by_index(sequence: EmailSequence, order_index: int) -> EmailSequenceStep | None:
    for step in _ordered_steps(sequence):
        if step.order_index == order_index:
            return step
    return None


def _get_first_step(sequence: EmailSequence) -> EmailSequenceStep | None:
    steps = _ordered_steps(sequence)
    if not steps:
        return None
    return steps[0]


def _get_next_step(sequence: EmailSequence, after_order_index: int) -> EmailSequenceStep | None:
    for step in _ordered_steps(sequence):
        if step.order_index > after_order_index:
            return step
    return None


def _active_enrollment(sequence_id: int, lead_id: int) -> EmailSequenceEnrollment | None:
    return EmailSequenceEnrollment.query.filter_by(
        sequence_id=sequence_id,
        lead_id=lead_id,
        status="active",
    ).first()


def _evaluate_step_condition(lead: Lead, condition: dict | None) -> bool:
    if not condition:
        return True
    if "stage_id" in condition and lead.stage_id != condition["stage_id"]:
        return False
    if "status" in condition and lead.status != condition["status"]:
        return False
    return True


def _trigger_config_matches(
    sequence: EmailSequence,
    trigger_type: str,
    payload: dict | None,
) -> bool:
    if sequence.trigger_type != trigger_type:
        return False
    config = sequence.trigger_config or {}
    payload = payload or {}

    if trigger_type == "on_stage_change":
        expected = config.get("stage_id")
        if expected is not None and payload.get("stage_id") != expected:
            return False

    if trigger_type == "on_segment_match":
        if config.get("segment_id") is not None and payload.get("segment_id") != config.get(
            "segment_id"
        ):
            return False

    return True


def _unsubscribe_url(lead_id: int, sequence_id: int) -> str:
    from flask import current_app, has_request_context

    token = generate_unsubscribe_token(lead_id, sequence_id)
    if has_request_context():
        return url_for("sequences.unsubscribe", token=token, _external=True)

    server_name = current_app.config.get("SERVER_NAME", "localhost")
    scheme = current_app.config.get("PREFERRED_URL_SCHEME", "http")
    with current_app.test_request_context():
        path = url_for("sequences.unsubscribe", token=token)
    return f"{scheme}://{server_name}{path}"


def append_unsubscribe_footer(
    body_html: str | None,
    body_text: str | None,
    *,
    lead_id: int,
    sequence_id: int,
) -> tuple[str, str]:
    link = _unsubscribe_url(lead_id, sequence_id)
    text_footer = f"\n\n---\nUnsubscribe: {link}"
    html_footer = (
        f'<p style="font-size:12px;color:#666;margin-top:24px;">'
        f'<a href="{link}">Unsubscribe</a> from this email sequence.</p>'
    )
    html = (body_html or "").strip()
    text = (body_text or "").strip()
    if html:
        html = html + html_footer
    else:
        html = f"<p>{text}</p>{html_footer}" if text else html_footer
    text = (text + text_footer) if text else text_footer.strip()
    return html, text


class SequenceService:
    @staticmethod
    def create_sequence(
        data: dict,
        organization_id: int,
        user_id: int | None,
    ) -> EmailSequence:
        name = (data.get("name") or "").strip()
        if not name:
            raise SequenceServiceError("Name is required.", "validation_error")
        trigger_type = data.get("trigger_type", "manual")
        if trigger_type not in TRIGGER_TYPES:
            raise SequenceServiceError("Invalid trigger type.", "validation_error")

        sequence = EmailSequence(
            organization_id=organization_id,
            name=name,
            description=(data.get("description") or "").strip() or None,
            is_active=bool(data.get("is_active", False)),
            trigger_type=trigger_type,
            trigger_config=data.get("trigger_config"),
            created_by=user_id,
        )
        db.session.add(sequence)
        db.session.flush()
        return sequence

    @staticmethod
    def update_sequence(
        sequence_id: int,
        data: dict,
        organization_id: int,
    ) -> EmailSequence:
        sequence = get_sequence_for_org(sequence_id, organization_id)
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                raise SequenceServiceError("Name is required.", "validation_error")
            sequence.name = name
        if "description" in data:
            sequence.description = (data.get("description") or "").strip() or None
        if "is_active" in data:
            sequence.is_active = bool(data["is_active"])
        if "trigger_type" in data:
            if data["trigger_type"] not in TRIGGER_TYPES:
                raise SequenceServiceError("Invalid trigger type.", "validation_error")
            sequence.trigger_type = data["trigger_type"]
        if "trigger_config" in data:
            sequence.trigger_config = data["trigger_config"]
        sequence.updated_at = _utc_now()
        db.session.flush()
        return sequence

    @staticmethod
    def delete_sequence(sequence_id: int, organization_id: int) -> None:
        sequence = get_sequence_for_org(sequence_id, organization_id)
        db.session.delete(sequence)

    @staticmethod
    def add_step(sequence_id: int, data: dict, organization_id: int) -> EmailSequenceStep:
        sequence = get_sequence_for_org(sequence_id, organization_id)
        order_index = data.get("order_index")
        if order_index is None:
            max_idx = max((s.order_index for s in sequence.steps), default=-1)
            order_index = max_idx + 1

        subject = (data.get("subject_template") or "").strip()
        if not subject:
            raise SequenceServiceError("Subject template is required.", "validation_error")

        step = EmailSequenceStep(
            sequence_id=sequence.id,
            order_index=int(order_index),
            delay_days=int(data.get("delay_days", 0) or 0),
            delay_hours=int(data.get("delay_hours", 0) or 0),
            subject_template=subject,
            body_html_template=data.get("body_html_template"),
            body_text_template=data.get("body_text_template"),
            condition=data.get("condition"),
        )
        db.session.add(step)
        db.session.flush()
        return step

    @staticmethod
    def update_step(
        sequence_id: int,
        step_id: int,
        data: dict,
        organization_id: int,
    ) -> EmailSequenceStep:
        sequence = get_sequence_for_org(sequence_id, organization_id)
        step = EmailSequenceStep.query.filter_by(id=step_id, sequence_id=sequence.id).first()
        if not step:
            raise SequenceServiceError("Step not found.", "not_found")

        for field in (
            "order_index",
            "delay_days",
            "delay_hours",
            "subject_template",
            "body_html_template",
            "body_text_template",
            "condition",
        ):
            if field in data:
                setattr(step, field, data[field])
        step.updated_at = _utc_now()
        db.session.flush()
        return step

    @staticmethod
    def delete_step(sequence_id: int, step_id: int, organization_id: int) -> None:
        sequence = get_sequence_for_org(sequence_id, organization_id)
        step = EmailSequenceStep.query.filter_by(id=step_id, sequence_id=sequence.id).first()
        if not step:
            raise SequenceServiceError("Step not found.", "not_found")
        db.session.delete(step)

    @staticmethod
    def reorder_steps(sequence_id: int, step_ids: list[int], organization_id: int) -> list[EmailSequenceStep]:
        sequence = get_sequence_for_org(sequence_id, organization_id)
        steps_by_id = {s.id: s for s in sequence.steps}
        for idx, step_id in enumerate(step_ids):
            step = steps_by_id.get(step_id)
            if not step:
                raise SequenceServiceError("Invalid step id in reorder list.", "validation_error")
            step.order_index = idx
        db.session.flush()
        return sorted(sequence.steps, key=lambda s: s.order_index)

    @staticmethod
    def enroll_lead(
        lead_id: int,
        sequence_id: int,
        enrolled_by: int | None = None,
        organization_id: int | None = None,
    ) -> EmailSequenceEnrollment:
        sequence = (
            EmailSequence.query.filter_by(id=sequence_id)
            .options(joinedload(EmailSequence.steps))
            .first()
        )
        if not sequence:
            raise SequenceServiceError("Sequence not found.", "not_found")

        org_id = organization_id if organization_id is not None else sequence.organization_id
        if sequence.organization_id != org_id:
            raise SequenceServiceError("Sequence not found.", "not_found")

        lead = get_lead_for_org(lead_id, org_id)
        if lead.unsubscribed:
            raise SequenceServiceError("Lead is unsubscribed.", "unsubscribed")

        if _active_enrollment(sequence.id, lead.id):
            raise SequenceServiceError(
                "Lead is already enrolled in this sequence.", "duplicate_enrollment"
            )

        first_step = _get_first_step(sequence)
        if not first_step:
            raise SequenceServiceError("Sequence has no steps.", "no_steps")

        now = _utc_now()
        enrollment = EmailSequenceEnrollment(
            sequence_id=sequence.id,
            lead_id=lead.id,
            organization_id=org_id,
            enrolled_by=enrolled_by,
            status="active",
            current_step_index=first_step.order_index,
            next_send_at=_calculate_next_send_at(first_step, now),
            enrolled_at=now,
        )
        db.session.add(enrollment)
        db.session.flush()

        _log_sequence_activity(
            lead.id,
            enrolled_by,
            "sequence_enrolled",
            content=sequence.name,
            metadata={"sequence_id": sequence.id, "enrollment_id": enrollment.id},
        )
        return enrollment

    @staticmethod
    def unenroll(
        enrollment_id: int,
        reason: str,
        organization_id: int | None = None,
    ) -> EmailSequenceEnrollment:
        enrollment = EmailSequenceEnrollment.query.filter_by(id=enrollment_id).first()
        if not enrollment:
            raise SequenceServiceError("Enrollment not found.", "not_found")
        if organization_id is not None and enrollment.organization_id != organization_id:
            raise SequenceServiceError("Enrollment not found.", "not_found")

        status = "unsubscribed" if reason == "unsubscribed" else "cancelled"
        if status not in ENROLLMENT_STATUSES:
            status = "cancelled"

        now = _utc_now()
        enrollment.status = status
        enrollment.cancelled_at = now
        enrollment.next_send_at = None
        enrollment.updated_at = now

        _log_sequence_activity(
            enrollment.lead_id,
            None,
            "sequence_unenrolled",
            content=reason,
            metadata={
                "sequence_id": enrollment.sequence_id,
                "enrollment_id": enrollment.id,
                "status": status,
            },
        )
        db.session.flush()
        return enrollment

    @staticmethod
    def handle_reply(lead_id: int) -> int:
        lead = db.session.get(Lead, lead_id)
        if not lead:
            return 0

        enrollments = EmailSequenceEnrollment.query.filter_by(
            lead_id=lead.id,
            status="active",
        ).all()
        cancelled = 0
        for enrollment in enrollments:
            sequence = db.session.get(EmailSequence, enrollment.sequence_id)
            if not sequence:
                continue
            config = sequence.trigger_config or {}
            if not config.get("stop_on_reply", False):
                continue
            SequenceService.unenroll(enrollment.id, "reply", organization_id=enrollment.organization_id)
            cancelled += 1
        return cancelled

    @staticmethod
    def trigger_auto_enroll(
        lead: Lead,
        trigger_type: str,
        payload: dict | None = None,
    ) -> int:
        if lead.unsubscribed:
            return 0

        sequences = EmailSequence.query.filter_by(
            organization_id=lead.organization_id,
            is_active=True,
            trigger_type=trigger_type,
        ).all()

        enrolled = 0
        for sequence in sequences:
            if not _trigger_config_matches(sequence, trigger_type, payload):
                continue
            try:
                SequenceService.enroll_lead(
                    lead.id,
                    sequence.id,
                    enrolled_by=None,
                    organization_id=lead.organization_id,
                )
                enrolled += 1
            except SequenceServiceError as exc:
                if exc.code == "duplicate_enrollment":
                    continue
                logger.warning(
                    "Auto-enroll skipped for lead %s sequence %s: %s",
                    lead.id,
                    sequence.id,
                    exc.message,
                )
        return enrolled

    @staticmethod
    def process_due_steps() -> int:
        now = _utc_now()
        due = (
            EmailSequenceEnrollment.query.filter(
                EmailSequenceEnrollment.status == "active",
                EmailSequenceEnrollment.next_send_at.isnot(None),
                EmailSequenceEnrollment.next_send_at <= now,
            )
            .order_by(EmailSequenceEnrollment.next_send_at.asc())
            .all()
        )

        processed = 0
        for enrollment in due:
            try:
                if SequenceService._process_single_enrollment(enrollment):
                    processed += 1
                db.session.commit()
            except Exception:
                logger.exception(
                    "Failed processing sequence enrollment %s", enrollment.id
                )
                db.session.rollback()
        return processed

    @staticmethod
    def _process_single_enrollment(enrollment: EmailSequenceEnrollment) -> bool:
        sequence = (
            EmailSequence.query.filter_by(id=enrollment.sequence_id)
            .options(joinedload(EmailSequence.steps))
            .first()
        )
        if not sequence or not sequence.is_active:
            SequenceService.unenroll(
                enrollment.id, "sequence_inactive", organization_id=enrollment.organization_id
            )
            return False

        lead = get_lead_for_org(enrollment.lead_id, enrollment.organization_id)
        if lead.unsubscribed:
            SequenceService.unenroll(
                enrollment.id, "unsubscribed", organization_id=enrollment.organization_id
            )
            return False

        if not lead.email:
            SequenceService.unenroll(
                enrollment.id, "no_email", organization_id=enrollment.organization_id
            )
            return False

        step = _get_step_by_index(sequence, enrollment.current_step_index)
        if not step:
            enrollment.status = "completed"
            enrollment.completed_at = _utc_now()
            enrollment.next_send_at = None
            enrollment.updated_at = _utc_now()
            return False

        if not _evaluate_step_condition(lead, step.condition):
            return SequenceService._advance_enrollment(enrollment, sequence, step, sent=False)

        subject = render_sequence_template(step.subject_template, lead)
        body_html = render_sequence_template(step.body_html_template, lead)
        body_text = render_sequence_template(step.body_text_template, lead)
        body_html, body_text = append_unsubscribe_footer(
            body_html,
            body_text,
            lead_id=lead.id,
            sequence_id=sequence.id,
        )

        try:
            result = EmailService.send_to_lead(
                lead.id,
                enrollment.enrolled_by,
                subject,
                body_html,
                body_text,
                organization_id=enrollment.organization_id,
            )
        except EmailServiceError as exc:
            logger.warning(
                "Sequence email failed for enrollment %s: %s", enrollment.id, exc.message
            )
            return False

        if not result.get("success"):
            return False

        sent = EmailSequenceSent(
            enrollment_id=enrollment.id,
            step_id=step.id,
            lead_id=lead.id,
            email_log_id=result.get("email_log_id"),
            sent_at=_utc_now(),
        )
        db.session.add(sent)

        _log_sequence_activity(
            lead.id,
            enrollment.enrolled_by,
            "sequence_email_sent",
            content=subject,
            metadata={
                "sequence_id": sequence.id,
                "enrollment_id": enrollment.id,
                "step_id": step.id,
                "email_log_id": result.get("email_log_id"),
            },
        )

        return SequenceService._advance_enrollment(enrollment, sequence, step, sent=True)

    @staticmethod
    def _advance_enrollment(
        enrollment: EmailSequenceEnrollment,
        sequence: EmailSequence,
        current_step: EmailSequenceStep,
        *,
        sent: bool,
    ) -> bool:
        next_step = _get_next_step(sequence, current_step.order_index)
        now = _utc_now()
        if next_step:
            enrollment.current_step_index = next_step.order_index
            enrollment.next_send_at = _calculate_next_send_at(next_step, now)
            enrollment.updated_at = now
            return sent
        enrollment.status = "completed"
        enrollment.completed_at = now
        enrollment.next_send_at = None
        enrollment.updated_at = now
        return sent

    @staticmethod
    def process_segment_match_enrollments() -> int:
        sequences = EmailSequence.query.filter_by(
            is_active=True, trigger_type="on_segment_match"
        ).all()
        enrolled_total = 0
        for sequence in sequences:
            filters = (sequence.trigger_config or {}).get("filters")
            if not filters:
                continue
            try:
                query = SegmentService.apply_filters(sequence.organization_id, filters)
                leads = query.all()
            except Exception:
                logger.exception(
                    "Segment match evaluation failed for sequence %s", sequence.id
                )
                continue
            for lead in leads:
                if lead.unsubscribed:
                    continue
                try:
                    SequenceService.enroll_lead(
                        lead.id,
                        sequence.id,
                        organization_id=sequence.organization_id,
                    )
                    enrolled_total += 1
                except SequenceServiceError as exc:
                    if exc.code != "duplicate_enrollment":
                        logger.warning(
                            "Segment enroll failed lead %s sequence %s: %s",
                            lead.id,
                            sequence.id,
                            exc.message,
                        )
        return enrolled_total

    @staticmethod
    def list_lead_enrollments(lead_id: int, organization_id: int) -> list[EmailSequenceEnrollment]:
        get_lead_for_org(lead_id, organization_id)
        return (
            EmailSequenceEnrollment.query.filter_by(
                lead_id=lead_id, organization_id=organization_id
            )
            .order_by(EmailSequenceEnrollment.enrolled_at.desc())
            .all()
        )

    @staticmethod
    def unsubscribe_lead(lead_id: int, sequence_id: int) -> None:
        lead = db.session.get(Lead, lead_id)
        sequence = db.session.get(EmailSequence, sequence_id)
        if not lead or not sequence:
            raise SequenceServiceError("Invalid unsubscribe token.", "invalid_token")
        if lead.organization_id != sequence.organization_id:
            raise SequenceServiceError("Invalid unsubscribe token.", "invalid_token")

        lead.unsubscribed = True
        active = EmailSequenceEnrollment.query.filter_by(
            lead_id=lead.id,
            sequence_id=sequence.id,
            status="active",
        ).all()
        for enrollment in active:
            SequenceService.unenroll(
                enrollment.id, "unsubscribed", organization_id=lead.organization_id
            )
