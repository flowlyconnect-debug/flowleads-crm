from __future__ import annotations

import json

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.core.errors import json_error, json_success, wants_json_response
from app.core.permissions import require_2fa, require_role
from app.extensions import db
from app.forms.embed import render_iframe_page
from app.forms.services import WebFormService, WebFormServiceError, get_active_form_by_token, get_form_for_org
from app.leads.models import PipelineStage
from app.leads.permissions import resolve_organization_id
from app.sequences.models import EmailSequence
from app.users.models import User

forms_bp = Blueprint("forms", __name__, url_prefix="/forms")

UI_ROLES = ("superadmin", "admin", "user")

DEFAULT_FIELDS = [
    {"key": "first_name", "label": "Etunimi", "type": "text", "required": True},
    {"key": "last_name", "label": "Sukunimi", "type": "text", "required": False},
    {"key": "email", "label": "Sähköposti", "type": "email", "required": True},
    {"key": "phone", "label": "Puhelin", "type": "tel", "required": False},
    {"key": "company", "label": "Yritys", "type": "text", "required": False},
]


def _require_ui_role():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in UI_ROLES:
        abort(403)


def _org_query_suffix(organization_id: int) -> dict:
    if current_user.is_superadmin():
        return {"organization_id": organization_id}
    return {}


def _builder_context(organization_id: int, form=None):
    stages = (
        PipelineStage.query.filter_by(organization_id=organization_id)
        .order_by(PipelineStage.order_index)
        .all()
    )
    users = (
        User.query.filter_by(organization_id=organization_id, is_active=True)
        .order_by(User.email)
        .all()
    )
    sequences = (
        EmailSequence.query.filter_by(organization_id=organization_id, is_active=True)
        .order_by(EmailSequence.name)
        .all()
    )
    embed_code = None
    if form:
        embed_code = WebFormService.generate_embed_code(form.id, organization_id)
    return {
        "stages": stages,
        "users": users,
        "sequences": sequences,
        "embed_code": embed_code,
        "default_fields_json": json.dumps(DEFAULT_FIELDS),
    }


@forms_bp.before_request
def _guard():
    if request.endpoint == "forms.iframe_embed":
        return
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    _require_ui_role()


@forms_bp.route("", methods=["GET"])
@require_role(*UI_ROLES)
@require_2fa
def list_forms():
    organization_id = resolve_organization_id()
    forms = WebFormService.list_for_organization(organization_id)
    return render_template(
        "forms/list.html",
        forms=forms,
        organization_id=organization_id,
        org_query=_org_query_suffix(organization_id),
    )


@forms_bp.route("", methods=["POST"])
@require_role("admin", "superadmin")
def create_form():
    organization_id = resolve_organization_id()
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = {
            "name": request.form.get("name"),
            "title": request.form.get("title"),
            "description": request.form.get("description"),
            "fields": DEFAULT_FIELDS,
        }
        if not (data.get("name") or "").strip() or not (data.get("title") or "").strip():
            flash("Nimi ja otsikko ovat pakollisia.", "danger")
            return redirect(url_for("forms.list_forms", **_org_query_suffix(organization_id)))
    if not data.get("fields"):
        data["fields"] = DEFAULT_FIELDS

    try:
        form = WebFormService.create_form(data, organization_id, current_user.id)
        db.session.commit()
        if wants_json_response():
            return json_success({"id": form.id, "form_token": form.form_token})
        flash("Lomake luotu.", "success")
        return redirect(
            url_for(
                "forms.edit_form",
                form_id=form.id,
                **_org_query_suffix(organization_id),
            )
        )
    except WebFormServiceError as exc:
        db.session.rollback()
        if wants_json_response():
            return json_error(exc.code, exc.message, 400)
        flash(exc.message, "danger")
        return redirect(url_for("forms.list_forms", **_org_query_suffix(organization_id)))


@forms_bp.route("/<int:form_id>/edit", methods=["GET"])
@require_role(*UI_ROLES)
@require_2fa
def edit_form(form_id: int):
    organization_id = resolve_organization_id()
    try:
        form = get_form_for_org(form_id, organization_id)
    except WebFormServiceError:
        abort(404)
    ctx = _builder_context(organization_id, form)
    return render_template(
        "forms/edit.html",
        form=form,
        fields_json=json.dumps(form.fields or []),
        organization_id=organization_id,
        org_query=_org_query_suffix(organization_id),
        **ctx,
    )


@forms_bp.route("/<int:form_id>", methods=["PUT", "POST"])
@require_role("admin", "superadmin")
def update_form(form_id: int):
    organization_id = resolve_organization_id()
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        raw_fields = request.form.get("fields_json", "[]")
        try:
            fields = json.loads(raw_fields)
        except json.JSONDecodeError:
            fields = None
        data = {
            "name": request.form.get("name"),
            "title": request.form.get("title"),
            "description": request.form.get("description"),
            "submit_button_text": request.form.get("submit_button_text"),
            "success_message": request.form.get("success_message"),
            "fields": fields,
            "default_stage_id": request.form.get("default_stage_id", type=int) or None,
            "default_assigned_to": request.form.get("default_assigned_to", type=int) or None,
            "auto_enroll_sequence_id": request.form.get("auto_enroll_sequence_id", type=int)
            or None,
            "notify_users": request.form.getlist("notify_users"),
            "is_active": request.form.get("is_active") == "1",
        }
        data = {k: v for k, v in data.items() if v is not None}

    try:
        form = WebFormService.update_form(form_id, data, organization_id)
        db.session.commit()
        if wants_json_response():
            return json_success({"id": form.id})
        flash("Lomake tallennettu.", "success")
    except WebFormServiceError as exc:
        db.session.rollback()
        if wants_json_response():
            return json_error(exc.code, exc.message, 400)
        flash(exc.message, "danger")
    return redirect(
        url_for("forms.edit_form", form_id=form_id, **_org_query_suffix(organization_id))
    )


@forms_bp.route("/<int:form_id>/delete", methods=["POST"])
@forms_bp.route("/<int:form_id>", methods=["DELETE"])
@require_role("admin", "superadmin")
def delete_form(form_id: int):
    organization_id = resolve_organization_id()
    try:
        WebFormService.soft_delete_form(form_id, organization_id)
        db.session.commit()
        if wants_json_response():
            return json_success()
        flash("Lomake poistettu.", "success")
    except WebFormServiceError as exc:
        db.session.rollback()
        if wants_json_response():
            return json_error(exc.code, exc.message, 404)
        flash(exc.message, "danger")
    return redirect(url_for("forms.list_forms", **_org_query_suffix(organization_id)))


@forms_bp.route("/<int:form_id>/submissions", methods=["GET"])
@require_role(*UI_ROLES)
@require_2fa
def form_submissions(form_id: int):
    organization_id = resolve_organization_id()
    try:
        form = get_form_for_org(form_id, organization_id)
        submissions = WebFormService.list_submissions(form_id, organization_id)
    except WebFormServiceError:
        abort(404)
    return render_template(
        "forms/submissions.html",
        form=form,
        submissions=submissions,
        organization_id=organization_id,
        org_query=_org_query_suffix(organization_id),
    )


@forms_bp.route("/<int:form_id>/embed-code", methods=["GET"])
@require_role(*UI_ROLES)
def embed_code(form_id: int):
    organization_id = resolve_organization_id()
    try:
        code = WebFormService.generate_embed_code(form_id, organization_id)
    except WebFormServiceError as exc:
        if wants_json_response():
            return json_error(exc.code, exc.message, 404)
        abort(404)
    if wants_json_response():
        return json_success(code)
    return jsonify(code)


@forms_bp.route("/<form_token>/embed", methods=["GET"])
def iframe_embed(form_token: str):
    form = get_active_form_by_token(form_token)
    if not form:
        abort(404)
    submit_url = url_for("forms_public_api.public_form_submit", form_token=form_token, _external=True)
    html = render_iframe_page(form, submit_url=submit_url)
    return Response(html, mimetype="text/html; charset=utf-8")


def register_forms_nav():
    pass
