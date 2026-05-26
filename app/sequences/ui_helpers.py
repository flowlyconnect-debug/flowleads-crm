"""UI helpers for email sequences (counts, stats, lead enrollment views)."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.leads.models import Lead
from app.sequences.models import (
    EmailSequence,
    EmailSequenceEnrollment,
    EmailSequenceSent,
    EmailSequenceStep,
)
from app.sequences.services import _ordered_steps, get_sequence_for_org

TRIGGER_LABELS_FI = {
    "manual": "Manuaalinen",
    "on_lead_created": "Uusi liidi",
    "on_stage_change": "Vaiheen muutos",
    "on_segment_match": "Segmentti",
}

ENROLLMENT_STATUS_LABELS_FI = {
    "active": "Aktiivinen",
    "completed": "Valmis",
    "cancelled": "Peruutettu",
    "unsubscribed": "Peru tilaus",
}


def trigger_label_fi(trigger_type: str) -> str:
    return TRIGGER_LABELS_FI.get(trigger_type, trigger_type)


def enrollment_status_label_fi(status: str) -> str:
    return ENROLLMENT_STATUS_LABELS_FI.get(status, status)


def enrollment_counts_for_sequence(sequence_id: int) -> dict[str, int]:
    rows = (
        db.session.query(
            EmailSequenceEnrollment.status,
            func.count(EmailSequenceEnrollment.id),
        )
        .filter_by(sequence_id=sequence_id)
        .group_by(EmailSequenceEnrollment.status)
        .all()
    )
    by_status = {status: count for status, count in rows}
    enrolled = sum(by_status.values())
    return {
        "enrolled": enrolled,
        "active": by_status.get("active", 0),
        "completed": by_status.get("completed", 0),
        "unsubscribed": by_status.get("unsubscribed", 0),
        "cancelled": by_status.get("cancelled", 0),
    }


def enrollment_counts_for_sequences(sequence_ids: list[int]) -> dict[int, dict[str, int]]:
    if not sequence_ids:
        return {}
    rows = (
        db.session.query(
            EmailSequenceEnrollment.sequence_id,
            EmailSequenceEnrollment.status,
            func.count(EmailSequenceEnrollment.id),
        )
        .filter(EmailSequenceEnrollment.sequence_id.in_(sequence_ids))
        .group_by(EmailSequenceEnrollment.sequence_id, EmailSequenceEnrollment.status)
        .all()
    )
    result: dict[int, dict[str, int]] = {
        sid: {"enrolled": 0, "active": 0, "completed": 0, "unsubscribed": 0, "cancelled": 0}
        for sid in sequence_ids
    }
    for sequence_id, status, count in rows:
        bucket = result[sequence_id]
        bucket["enrolled"] += count
        if status == "active":
            bucket["active"] = count
        elif status == "completed":
            bucket["completed"] = count
        elif status == "unsubscribed":
            bucket["unsubscribed"] = count
        elif status == "cancelled":
            bucket["cancelled"] = count
    return result


def sequence_stats(sequence_id: int, organization_id: int) -> dict:
    get_sequence_for_org(sequence_id, organization_id)
    totals = enrollment_counts_for_sequence(sequence_id)
    steps = (
        EmailSequenceStep.query.filter_by(sequence_id=sequence_id)
        .order_by(EmailSequenceStep.order_index.asc())
        .all()
    )
    sent_messages = (
        db.session.query(EmailSequenceSent)
        .join(
            EmailSequenceEnrollment,
            EmailSequenceSent.enrollment_id == EmailSequenceEnrollment.id,
        )
        .filter(EmailSequenceEnrollment.sequence_id == sequence_id)
        .all()
    )
    sent_by_step: dict[int, dict[str, int]] = {}
    for sent in sent_messages:
        bucket = sent_by_step.setdefault(
            sent.step_id, {"sent": 0, "opened": 0, "clicked": 0}
        )
        bucket["sent"] += 1
        if sent.opened_at is not None:
            bucket["opened"] += 1
        if sent.clicked_at is not None:
            bucket["clicked"] += 1
    step_stats = []
    for idx, step in enumerate(steps, start=1):
        metrics = sent_by_step.get(step.id, {"sent": 0, "opened": 0, "clicked": 0})
        step_stats.append(
            {
                "step_id": step.id,
                "step_number": idx,
                "order_index": step.order_index,
                "subject_preview": (step.subject_template or "")[:80],
                "sent": metrics["sent"],
                "opened": metrics["opened"],
                "clicked": metrics["clicked"],
            }
        )
    return {**totals, "steps": step_stats}


def step_number_for_index(steps: list[EmailSequenceStep], order_index: int) -> int | None:
    ordered = sorted(steps, key=lambda s: s.order_index)
    for idx, step in enumerate(ordered, start=1):
        if step.order_index == order_index:
            return idx
    return None


def lead_enrollment_rows(lead_id: int, organization_id: int) -> list[dict]:
    enrollments = (
        EmailSequenceEnrollment.query.filter_by(
            lead_id=lead_id, organization_id=organization_id
        )
        .options(joinedload(EmailSequenceEnrollment.sequence).joinedload(EmailSequence.steps))
        .order_by(EmailSequenceEnrollment.enrolled_at.desc())
        .all()
    )
    rows = []
    for enrollment in enrollments:
        sequence = enrollment.sequence
        steps = _ordered_steps(sequence) if sequence else []
        step_num = step_number_for_index(steps, enrollment.current_step_index)
        current_step = next(
            (s for s in steps if s.order_index == enrollment.current_step_index), None
        )
        rows.append(
            {
                "enrollment": enrollment,
                "sequence": sequence,
                "sequence_name": sequence.name if sequence else "—",
                "status_label": enrollment_status_label_fi(enrollment.status),
                "step_number": step_num,
                "step_total": len(steps),
                "current_subject": (current_step.subject_template[:60] + "…")
                if current_step and len(current_step.subject_template) > 60
                else (current_step.subject_template if current_step else None),
                "next_send_at": enrollment.next_send_at,
            }
        )
    return rows


def available_sequences_for_enroll(organization_id: int) -> list[EmailSequence]:
    sequences = (
        EmailSequence.query.filter_by(organization_id=organization_id, is_active=True)
        .options(joinedload(EmailSequence.steps))
        .order_by(EmailSequence.name.asc())
        .all()
    )
    return [s for s in sequences if s.steps]


def preview_leads(organization_id: int, limit: int = 50) -> list[Lead]:
    return (
        Lead.query.filter_by(organization_id=organization_id, status="active")
        .filter(Lead.email.isnot(None))
        .order_by(Lead.updated_at.desc())
        .limit(limit)
        .all()
    )
