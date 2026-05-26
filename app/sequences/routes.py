from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.errors import json_error, json_success, wants_json_response
from app.extensions import db
from app.leads.permissions import resolve_organization_id
from app.leads.services import get_lead_for_org
from app.sequences.models import EmailSequence, TRIGGER_TYPES
from app.sequences.services import (
    SequenceService,
    SequenceServiceError,
    get_sequence_for_org,
    render_sequence_template,
)
from app.sequences.tokens import verify_unsubscribe_token
from app.sequences.ui_helpers import (
    available_sequences_for_enroll,
    enrollment_counts_for_sequence,
    enrollment_counts_for_sequences,
    preview_leads,
    sequence_stats,
    trigger_label_fi,
)

sequences_bp = Blueprint("sequences", __name__)

UI_ROLES = ("superadmin", "admin", "user")


def _require_ui_role():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in UI_ROLES:
        abort(403)


def _org_query_suffix(organization_id: int) -> dict:
    if current_user.is_superadmin():
        return {"organization_id": organization_id}
    return {}


def _serialize_sequence(sequence) -> dict:
    return {
        "id": sequence.id,
        "organization_id": sequence.organization_id,
        "name": sequence.name,
        "description": sequence.description,
        "is_active": sequence.is_active,
        "trigger_type": sequence.trigger_type,
        "trigger_config": sequence.trigger_config,
        "created_by": sequence.created_by,
        "created_at": sequence.created_at.isoformat() if sequence.created_at else None,
        "updated_at": sequence.updated_at.isoformat() if sequence.updated_at else None,
        "steps": [
            {
                "id": s.id,
                "order_index": s.order_index,
                "delay_days": s.delay_days,
                "delay_hours": s.delay_hours,
                "subject_template": s.subject_template,
                "body_html_template": s.body_html_template,
                "body_text_template": s.body_text_template,
                "condition": s.condition,
                "skip_if_replied": bool((s.condition or {}).get("skip_if_replied")),
            }
            for s in sorted(sequence.steps, key=lambda x: x.order_index)
        ],
    }


def _serialize_enrollment(enrollment) -> dict:
    return {
        "id": enrollment.id,
        "sequence_id": enrollment.sequence_id,
        "lead_id": enrollment.lead_id,
        "status": enrollment.status,
        "current_step_index": enrollment.current_step_index,
        "next_send_at": enrollment.next_send_at.isoformat() if enrollment.next_send_at else None,
        "enrolled_at": enrollment.enrolled_at.isoformat() if enrollment.enrolled_at else None,
        "completed_at": enrollment.completed_at.isoformat() if enrollment.completed_at else None,
        "cancelled_at": enrollment.cancelled_at.isoformat() if enrollment.cancelled_at else None,
    }


def _condition_from_payload(payload: dict) -> dict | None:
    if payload.get("skip_if_replied") in (True, "true", "1", "on", 1):
        return {"skip_if_replied": True}
    if "condition" in payload and payload["condition"] is not None:
        return payload["condition"]
    return None


def _step_payload_from_request(payload: dict) -> dict:
    data = dict(payload)
    if "skip_if_replied" in payload:
        data["condition"] = _condition_from_payload(payload)
        data.pop("skip_if_replied", None)
    return data


def _handle_error(exc: SequenceServiceError):
    status = 404 if exc.code == "not_found" else 400
    if exc.code == "unsubscribed":
        status = 400
    return json_error(exc.code, exc.message, status)


@sequences_bp.before_request
def _sequences_auth():
    if request.endpoint == "sequences.unsubscribe":
        return
    if not current_user.is_authenticated:
        abort(401)
    _require_ui_role()


@sequences_bp.route("/sequences", methods=["GET"])
@login_required
def list_sequences():
    organization_id = resolve_organization_id()
    sequences = (
        EmailSequence.query.filter_by(organization_id=organization_id)
        .order_by(EmailSequence.name.asc())
        .all()
    )
    if wants_json_response():
        data = [_serialize_sequence(s) for s in sequences]
        return json_success({"sequences": data})

    counts = enrollment_counts_for_sequences([s.id for s in sequences])
    rows = []
    for seq in sequences:
        c = counts.get(seq.id, {})
        rows.append(
            {
                "sequence": seq,
                "counts": c,
                "trigger_label": trigger_label_fi(seq.trigger_type),
            }
        )
    org_query = _org_query_suffix(organization_id)
    return render_template(
        "sequences/list.html",
        sequences=rows,
        organization_id=organization_id,
        org_query=org_query,
        trigger_types=TRIGGER_TYPES,
    )


@sequences_bp.route("/sequences", methods=["POST"])
@login_required
def create_sequence():
    organization_id = resolve_organization_id()
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    try:
        sequence = SequenceService.create_sequence(
            payload or {},
            organization_id,
            current_user.id,
        )
        db.session.commit()
    except SequenceServiceError as exc:
        db.session.rollback()
        if wants_json_response() or request.is_json:
            return _handle_error(exc)
        flash(exc.message, "danger")
        return redirect(url_for("sequences.list_sequences", **_org_query_suffix(organization_id)))

    org_query = _org_query_suffix(organization_id)
    if wants_json_response() or request.is_json:
        return json_success(_serialize_sequence(sequence), 201)
    return redirect(url_for("sequences.sequence_builder", sequence_id=sequence.id, **org_query))


@sequences_bp.route("/sequences/<int:sequence_id>", methods=["GET"])
@login_required
def get_sequence(sequence_id):
    organization_id = resolve_organization_id()
    try:
        sequence = get_sequence_for_org(sequence_id, organization_id)
    except SequenceServiceError as exc:
        if wants_json_response():
            return _handle_error(exc)
        abort(404)

    if wants_json_response():
        return json_success(_serialize_sequence(sequence))

    return redirect(
        url_for(
            "sequences.sequence_builder",
            sequence_id=sequence.id,
            **_org_query_suffix(organization_id),
        )
    )


@sequences_bp.route("/sequences/<int:sequence_id>/builder", methods=["GET"])
@login_required
def sequence_builder(sequence_id):
    organization_id = resolve_organization_id()
    try:
        sequence = get_sequence_for_org(sequence_id, organization_id)
    except SequenceServiceError:
        abort(404)

    steps = sorted(sequence.steps, key=lambda s: s.order_index)
    org_query = _org_query_suffix(organization_id)
    leads = preview_leads(organization_id)
    counts = enrollment_counts_for_sequence(sequence.id)
    return render_template(
        "sequences/builder.html",
        sequence=sequence,
        steps=steps,
        organization_id=organization_id,
        org_query=org_query,
        preview_leads=leads,
        trigger_label=trigger_label_fi(sequence.trigger_type),
        enrollment_counts=counts,
    )


@sequences_bp.route("/sequences/<int:sequence_id>/stats", methods=["GET"])
@login_required
def sequence_stats_page(sequence_id):
    organization_id = resolve_organization_id()
    try:
        sequence = get_sequence_for_org(sequence_id, organization_id)
    except SequenceServiceError:
        abort(404)

    stats = sequence_stats(sequence.id, organization_id)
    org_query = _org_query_suffix(organization_id)

    if wants_json_response():
        return json_success({"sequence_id": sequence.id, "name": sequence.name, **stats})

    return render_template(
        "sequences/stats.html",
        sequence=sequence,
        stats=stats,
        organization_id=organization_id,
        org_query=org_query,
    )


@sequences_bp.route("/sequences/<int:sequence_id>/preview", methods=["POST"])
@login_required
def sequence_preview(sequence_id):
    organization_id = resolve_organization_id()
    payload = request.get_json(silent=True) or {}
    lead_id = payload.get("lead_id")
    step_id = payload.get("step_id")
    if not lead_id:
        return json_error("validation_error", "lead_id is required.", 400)

    try:
        sequence = get_sequence_for_org(sequence_id, organization_id)
        lead = get_lead_for_org(int(lead_id), organization_id)
    except SequenceServiceError as exc:
        return _handle_error(exc)

    step = None
    if step_id:
        step = next((s for s in sequence.steps if s.id == int(step_id)), None)
        if not step:
            return json_error("not_found", "Step not found.", 404)
    elif sequence.steps:
        step = sorted(sequence.steps, key=lambda s: s.order_index)[0]

    if not step:
        return json_error("validation_error", "Sequence has no steps.", 400)

    return json_success(
        {
            "subject": render_sequence_template(step.subject_template, lead),
            "body_html": render_sequence_template(step.body_html_template, lead),
            "body_text": render_sequence_template(step.body_text_template, lead),
            "lead": {
                "id": lead.id,
                "display_name": lead.display_name,
                "email": lead.email,
            },
        }
    )


@sequences_bp.route("/sequences/<int:sequence_id>", methods=["PUT"])
@login_required
def update_sequence(sequence_id):
    organization_id = resolve_organization_id()
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    try:
        sequence = SequenceService.update_sequence(sequence_id, payload or {}, organization_id)
        db.session.commit()
    except SequenceServiceError as exc:
        db.session.rollback()
        return _handle_error(exc)
    return json_success(_serialize_sequence(sequence))


@sequences_bp.route("/sequences/<int:sequence_id>", methods=["DELETE"])
@login_required
def delete_sequence(sequence_id):
    organization_id = resolve_organization_id()
    try:
        SequenceService.delete_sequence(sequence_id, organization_id)
        db.session.commit()
    except SequenceServiceError as exc:
        db.session.rollback()
        return _handle_error(exc)
    return json_success({"deleted": True})


@sequences_bp.route("/sequences/<int:sequence_id>/steps", methods=["POST"])
@login_required
def add_step(sequence_id):
    organization_id = resolve_organization_id()
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    try:
        step = SequenceService.add_step(
            sequence_id,
            _step_payload_from_request(payload or {}),
            organization_id,
        )
        db.session.commit()
        sequence = get_sequence_for_org(sequence_id, organization_id)
    except SequenceServiceError as exc:
        db.session.rollback()
        return _handle_error(exc)
    return json_success(_serialize_sequence(sequence), 201)


@sequences_bp.route("/sequences/<int:sequence_id>/steps/<int:step_id>", methods=["PUT"])
@login_required
def update_step(sequence_id, step_id):
    organization_id = resolve_organization_id()
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    try:
        SequenceService.update_step(
            sequence_id,
            step_id,
            _step_payload_from_request(payload or {}),
            organization_id,
        )
        db.session.commit()
        sequence = get_sequence_for_org(sequence_id, organization_id)
    except SequenceServiceError as exc:
        db.session.rollback()
        return _handle_error(exc)
    return json_success(_serialize_sequence(sequence))


@sequences_bp.route("/sequences/<int:sequence_id>/steps/<int:step_id>", methods=["DELETE"])
@login_required
def delete_step_route(sequence_id, step_id):
    organization_id = resolve_organization_id()
    try:
        SequenceService.delete_step(sequence_id, step_id, organization_id)
        db.session.commit()
    except SequenceServiceError as exc:
        db.session.rollback()
        return _handle_error(exc)
    return json_success({"deleted": True})


@sequences_bp.route("/sequences/<int:sequence_id>/steps/reorder", methods=["POST"])
@login_required
def reorder_steps(sequence_id):
    organization_id = resolve_organization_id()
    payload = request.get_json(silent=True) or {}
    step_ids = payload.get("step_ids") or []
    try:
        SequenceService.reorder_steps(sequence_id, step_ids, organization_id)
        db.session.commit()
        sequence = get_sequence_for_org(sequence_id, organization_id)
    except SequenceServiceError as exc:
        db.session.rollback()
        return _handle_error(exc)
    return json_success(_serialize_sequence(sequence))


@sequences_bp.route("/leads/<int:lead_id>/sequences/enroll", methods=["POST"])
@login_required
def enroll_lead(lead_id):
    organization_id = resolve_organization_id()
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    sequence_id = payload.get("sequence_id") if payload else None
    if not sequence_id:
        return json_error("validation_error", "sequence_id is required.", 400)
    try:
        enrollment = SequenceService.enroll_lead(
            lead_id,
            int(sequence_id),
            enrolled_by=current_user.id,
            organization_id=organization_id,
        )
        db.session.commit()
    except SequenceServiceError as exc:
        db.session.rollback()
        return _handle_error(exc)
    return json_success(_serialize_enrollment(enrollment), 201)


@sequences_bp.route("/leads/<int:lead_id>/sequences/unenroll", methods=["POST"])
@login_required
def unenroll_lead(lead_id):
    organization_id = resolve_organization_id()
    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    enrollment_id = payload.get("enrollment_id") if payload else None
    reason = (payload or {}).get("reason", "cancelled")
    if not enrollment_id:
        return json_error("validation_error", "enrollment_id is required.", 400)
    try:
        enrollment = SequenceService.unenroll(
            int(enrollment_id),
            reason,
            organization_id=organization_id,
        )
        db.session.commit()
    except SequenceServiceError as exc:
        db.session.rollback()
        return _handle_error(exc)
    return json_success(_serialize_enrollment(enrollment))


@sequences_bp.route("/leads/<int:lead_id>/sequences", methods=["GET"])
@login_required
def list_lead_sequences(lead_id):
    organization_id = resolve_organization_id()
    try:
        enrollments = SequenceService.list_lead_enrollments(lead_id, organization_id)
    except SequenceServiceError as exc:
        return _handle_error(exc)
    return json_success({"enrollments": [_serialize_enrollment(e) for e in enrollments]})


@sequences_bp.route("/unsubscribe", methods=["GET"])
def unsubscribe():
    token = request.args.get("token", "")
    data = verify_unsubscribe_token(token)
    if not data:
        return render_template("sequences/unsubscribe.html", success=False), 400
    try:
        SequenceService.unsubscribe_lead(data["lead_id"], data["sequence_id"])
        db.session.commit()
    except SequenceServiceError:
        db.session.rollback()
        return render_template("sequences/unsubscribe.html", success=False), 400
    return render_template("sequences/unsubscribe.html", success=True)
