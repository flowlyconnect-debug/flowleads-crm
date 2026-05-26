import json

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.automations.constants import (
    ACTION_LABELS_FI,
    ACTION_TYPES,
    TRIGGER_DESCRIPTIONS_FI,
    TRIGGER_LABELS_FI,
    TRIGGER_TYPES,
)
from app.automations.models import Automation
from app.automations.services import AutomationService, AutomationServiceError
from app.email.models import EmailTemplate
from app.extensions import db
from app.leads.models import PipelineStage
from app.leads.permissions import resolve_organization_id
from app.sequences.models import EmailSequence
from app.users.models import User

automations_bp = Blueprint("automations", __name__, url_prefix="/automations")

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


def _parse_actions_from_form(form) -> list[dict]:
    raw = form.get("actions_json") or "[]"
    try:
        actions = json.loads(raw)
    except json.JSONDecodeError:
        actions = []
    result = []
    for item in actions:
        action_type = item.get("action_type")
        if action_type not in ACTION_TYPES:
            continue
        config = item.get("action_config") or {}
        if action_type == "send_webhook" and item.get("secret_headers"):
            config["secret_headers"] = item["secret_headers"]
        result.append({"action_type": action_type, "action_config": config})
    return result


def _parse_trigger_config(trigger_type: str, form) -> dict:
    config: dict = {}
    if trigger_type == "lead_stage_changed":
        if form.get("to_stage_id"):
            config["to_stage_id"] = int(form.get("to_stage_id"))
        if form.get("from_stage_id"):
            config["from_stage_id"] = int(form.get("from_stage_id"))
    elif trigger_type == "lead_score_changed":
        config["threshold"] = int(form.get("threshold") or 80)
        config["operator"] = form.get("operator") or "crosses_above"
    elif trigger_type == "lead_no_activity":
        config["days"] = int(form.get("days") or 14)
        stages_raw = form.get("stages") or ""
        if stages_raw.strip():
            config["stages"] = [int(s) for s in stages_raw.split(",") if s.strip().isdigit()]
    elif trigger_type == "lead_tag_added":
        if form.get("tag"):
            config["tag"] = form.get("tag").strip()
    elif trigger_type == "task_overdue":
        config["hours"] = int(form.get("hours") or 0)
    elif trigger_type == "sequence_completed":
        if form.get("sequence_id"):
            config["sequence_id"] = int(form.get("sequence_id"))
    min_score = form.get("min_score")
    if min_score:
        config["min_score"] = int(min_score)
    source = form.get("source")
    if source:
        config["source"] = [s.strip() for s in source.split(",") if s.strip()]
    return config


@automations_bp.before_request
@login_required
def _guard():
    _require_ui_role()


@automations_bp.route("")
def list_automations():
    organization_id = resolve_organization_id()
    automations = AutomationService.list_automations(organization_id)
    rows = []
    for automation in automations:
        rows.append(
            {
                "automation": automation,
                "trigger_label": TRIGGER_LABELS_FI.get(
                    automation.trigger_type, automation.trigger_type
                ),
                "action_count": len(automation.actions),
            }
        )
    return render_template(
        "automations/list.html",
        rows=rows,
        org_query=_org_query_suffix(organization_id),
    )


@automations_bp.route("/new", methods=["GET", "POST"])
def create_automation():
    organization_id = resolve_organization_id()
    org_query = _org_query_suffix(organization_id)
    stages = PipelineStage.query.filter_by(organization_id=organization_id).order_by(
        PipelineStage.order_index
    ).all()
    sequences = EmailSequence.query.filter_by(organization_id=organization_id).order_by(
        EmailSequence.name
    ).all()
    users = User.query.filter_by(organization_id=organization_id, is_active=True).all()
    templates = EmailTemplate.query.filter_by(organization_id=organization_id).all()

    if request.method == "POST":
        trigger_type = request.form.get("trigger_type")
        try:
            automation = AutomationService.create(
                {
                    "name": request.form.get("name"),
                    "description": request.form.get("description"),
                    "is_active": request.form.get("is_active") == "on",
                    "trigger_type": trigger_type,
                    "trigger_config": _parse_trigger_config(trigger_type, request.form),
                    "actions": _parse_actions_from_form(request.form),
                },
                organization_id,
                current_user.id,
            )
            db.session.commit()
            flash("Automaatio luotu.", "success")
            return redirect(
                url_for("automations.edit_automation", automation_id=automation.id, **org_query)
            )
        except AutomationServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")

    return render_template(
        "automations/form.html",
        automation=None,
        trigger_types=TRIGGER_TYPES,
        trigger_labels=TRIGGER_LABELS_FI,
        trigger_descriptions=TRIGGER_DESCRIPTIONS_FI,
        action_types=ACTION_TYPES,
        action_labels=ACTION_LABELS_FI,
        stages=stages,
        sequences=sequences,
        users=users,
        templates=templates,
        org_query=org_query,
    )


@automations_bp.route("/<int:automation_id>/edit", methods=["GET", "POST"])
def edit_automation(automation_id: int):
    organization_id = resolve_organization_id()
    org_query = _org_query_suffix(organization_id)
    automation = Automation.query.filter_by(
        id=automation_id, organization_id=organization_id
    ).first()
    if not automation:
        abort(404)

    stages = PipelineStage.query.filter_by(organization_id=organization_id).order_by(
        PipelineStage.order_index
    ).all()
    sequences = EmailSequence.query.filter_by(organization_id=organization_id).order_by(
        EmailSequence.name
    ).all()
    users = User.query.filter_by(organization_id=organization_id, is_active=True).all()
    templates = EmailTemplate.query.filter_by(organization_id=organization_id).all()

    if request.method == "POST":
        trigger_type = request.form.get("trigger_type") or automation.trigger_type
        try:
            AutomationService.update(
                automation_id,
                {
                    "name": request.form.get("name"),
                    "description": request.form.get("description"),
                    "is_active": request.form.get("is_active") == "on",
                    "trigger_type": trigger_type,
                    "trigger_config": _parse_trigger_config(trigger_type, request.form),
                    "actions": _parse_actions_from_form(request.form),
                },
                organization_id,
            )
            db.session.commit()
            flash("Automaatio tallennettu.", "success")
            return redirect(
                url_for("automations.edit_automation", automation_id=automation_id, **org_query)
            )
        except AutomationServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")

    return render_template(
        "automations/form.html",
        automation=automation,
        trigger_types=TRIGGER_TYPES,
        trigger_labels=TRIGGER_LABELS_FI,
        trigger_descriptions=TRIGGER_DESCRIPTIONS_FI,
        action_types=ACTION_TYPES,
        action_labels=ACTION_LABELS_FI,
        stages=stages,
        sequences=sequences,
        users=users,
        templates=templates,
        org_query=org_query,
    )


@automations_bp.route("/<int:automation_id>/toggle", methods=["POST"])
def toggle_automation(automation_id: int):
    organization_id = resolve_organization_id()
    org_query = _org_query_suffix(organization_id)
    try:
        automation = AutomationService.toggle_active(automation_id, organization_id)
        db.session.commit()
        state = "aktivoitu" if automation.is_active else "deaktivoitu"
        flash(f"Automaatio {state}.", "success")
    except AutomationServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("automations.list_automations", **org_query))


@automations_bp.route("/<int:automation_id>/logs")
def automation_logs(automation_id: int):
    organization_id = resolve_organization_id()
    org_query = _org_query_suffix(organization_id)
    automation = Automation.query.filter_by(
        id=automation_id, organization_id=organization_id
    ).first()
    if not automation:
        abort(404)
    logs = AutomationService.get_logs(automation_id, organization_id, limit=50)
    return render_template(
        "automations/logs.html",
        automation=automation,
        logs=logs,
        org_query=org_query,
    )
