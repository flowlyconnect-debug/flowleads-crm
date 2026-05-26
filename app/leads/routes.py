import json
from datetime import datetime, timezone

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from flask_wtf.csrf import generate_csrf

from app.core.errors import json_error, json_success, wants_json_response
from app.extensions import db
from app.leads.forms import BulkActionForm, LeadFilterForm, LeadForm, QuickNoteForm
from app.leads.models import Activity, Lead, PipelineStage
from app.leads.permissions import (
    can_archive_leads,
    can_assign_to_others,
    resolve_organization_id,
    validate_assignment,
)
from app.leads.services import LeadService, LeadServiceError, get_default_stage, get_lead_for_org
from app.users.models import User

leads_bp = Blueprint("leads", __name__, url_prefix="/leads")

UI_ROLES = ("superadmin", "admin", "user")


def _require_ui_role():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in UI_ROLES:
        abort(403)


def _filters_from_request() -> dict:
    return {
        "search": request.args.get("search", ""),
        "stage_id": request.args.get("stage_id", type=int) or None,
        "source": request.args.get("source") or None,
        "assigned_to": request.args.get("assigned_to", type=int) or None,
        "status": request.args.get("status") or None,
        "score_min": request.args.get("score_min"),
        "score_max": request.args.get("score_max"),
        "created_from": _parse_date(request.args.get("created_from")),
        "created_to": _parse_date(request.args.get("created_to"), end_of_day=True),
        "sort": request.args.get("sort", "created_at"),
        "dir": request.args.get("dir", "desc"),
    }


def _parse_date(value, end_of_day: bool = False):
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _org_users(organization_id: int):
    return User.query.filter_by(organization_id=organization_id, is_active=True).order_by(User.email).all()


def _populate_lead_form_choices(form: LeadForm, organization_id: int):
    stages = PipelineStage.query.filter_by(organization_id=organization_id).order_by(
        PipelineStage.order_index
    ).all()
    form.stage_id.choices = [(s.id, s.name) for s in stages]
    users = _org_users(organization_id)
    form.assigned_to.choices = [(0, "Unassigned")] + [(u.id, u.email) for u in users]


def _lead_to_form_data(form: LeadForm, lead: Lead):
    form.first_name.data = lead.first_name
    form.last_name.data = lead.last_name
    form.email.data = lead.email
    form.phone.data = lead.phone
    form.company.data = lead.company
    form.title.data = lead.title
    form.website.data = lead.website
    form.linkedin_url.data = lead.linkedin_url
    form.stage_id.data = lead.stage_id
    form.assigned_to.data = lead.assigned_to or ""
    form.source.data = lead.source
    form.source_ref.data = lead.source_ref
    form.score.data = lead.score
    form.score_reason.data = lead.score_reason
    form.notes.data = lead.notes
    form.tags.data = ", ".join(lead.tags or [])


def _form_to_data(form: LeadForm) -> dict:
    data = {
        "first_name": form.first_name.data,
        "last_name": form.last_name.data,
        "email": form.email.data,
        "phone": form.phone.data,
        "company": form.company.data,
        "title": form.title.data,
        "website": form.website.data,
        "linkedin_url": form.linkedin_url.data,
        "source": form.source.data,
        "source_ref": form.source_ref.data,
        "score": form.score.data,
        "score_reason": form.score_reason.data,
        "notes": form.notes.data,
        "tags": form.tags.data,
    }
    if form.stage_id.data:
        data["stage_id"] = form.stage_id.data
    if form.assigned_to.data not in (None, "", 0):
        data["assigned_to"] = validate_assignment(form.assigned_to.data)
    elif can_assign_to_others():
        data["assigned_to"] = None
    else:
        data["assigned_to"] = current_user.id if request.method == "POST" and not request.path.endswith("/leads") else None
    return data


def _handle_service_error(exc: LeadServiceError, *, json_response: bool = False):
    if json_response:
        return json_error(exc.code, exc.message, 400)
    flash(exc.message, "danger")
    return None


@leads_bp.before_request
@login_required
def block_api_client():
    _require_ui_role()


@leads_bp.route("")
def list_leads():
    organization_id = resolve_organization_id()
    filters = _filters_from_request()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    pagination = LeadService.search(organization_id, filters, page=page, per_page=25)
    form_args = {k: v for k, v in request.args.items() if v != ""}
    filter_form = LeadFilterForm(form_args, meta={"csrf": False})
    stages = PipelineStage.query.filter_by(organization_id=organization_id).order_by(
        PipelineStage.order_index
    ).all()
    filter_form.stage_id.choices = [(0, "All stages")] + [(s.id, s.name) for s in stages]
    filter_form.stage_id.coerce = lambda v: int(v) if v not in (None, "", 0) else 0
    users = _org_users(organization_id)
    filter_form.assigned_to.choices = [(0, "Anyone")] + [(u.id, u.email) for u in users]
    filter_form.assigned_to.coerce = lambda v: int(v) if v not in (None, "", 0) else 0

    bulk_form = BulkActionForm()
    bulk_form.stage_id.choices = [(s.id, s.name) for s in stages]
    bulk_form.assigned_to.choices = [(u.id, u.email) for u in users]

    return render_template(
        "leads/list.html",
        pagination=pagination,
        leads=pagination.items,
        filter_form=filter_form,
        filters=filters,
        bulk_form=bulk_form,
        organization_id=organization_id,
        can_archive=can_archive_leads(),
        can_assign_others=can_assign_to_others(),
    )


@leads_bp.route("/pipeline")
def pipeline():
    organization_id = resolve_organization_id()
    filters = {
        "assigned_to": request.args.get("assigned_to"),
        "source": request.args.get("source"),
        "score_min": request.args.get("score_min"),
        "score_max": request.args.get("score_max"),
        "created_from": _parse_date(request.args.get("created_from")),
        "created_to": _parse_date(request.args.get("created_to"), end_of_day=True),
    }
    data = LeadService.get_pipeline_data(organization_id, filters)
    users = _org_users(organization_id)
    return render_template(
        "leads/pipeline.html",
        stages=data["stages"],
        leads_by_stage=data["leads_by_stage"],
        users=users,
        filters=filters,
        organization_id=organization_id,
        csrf_token=generate_csrf,
    )


@leads_bp.route("/<int:lead_id>")
def detail(lead_id):
    organization_id = resolve_organization_id()
    try:
        lead = get_lead_for_org(lead_id, organization_id)
    except LeadServiceError as exc:
        abort(404 if exc.code == "not_found" else 400)

    activities = (
        Activity.query.filter_by(lead_id=lead.id, organization_id=organization_id)
        .order_by(Activity.created_at.desc())
        .all()
    )
    stages = PipelineStage.query.filter_by(organization_id=organization_id).order_by(
        PipelineStage.order_index
    ).all()
    users = _org_users(organization_id)
    note_form = QuickNoteForm()
    return render_template(
        "leads/detail.html",
        lead=lead,
        activities=activities,
        stages=stages,
        users=users,
        note_form=note_form,
        organization_id=organization_id,
        can_archive=can_archive_leads(),
        can_assign_others=can_assign_to_others(),
    )


@leads_bp.route("", methods=["POST"])
def create_lead():
    organization_id = resolve_organization_id()
    form = LeadForm()
    _populate_lead_form_choices(form, organization_id)
    if not form.validate_on_submit():
        return render_template("leads/form.html", form=form, lead=None, organization_id=organization_id), 400

    data = _form_to_data(form)
    if not can_assign_to_others() and not data.get("assigned_to"):
        data["assigned_to"] = current_user.id

    try:
        lead = LeadService.create(
            data, organization_id, current_user.id, actor_role=current_user.role
        )
        db.session.commit()
        flash("Lead created successfully.", "success")
        return redirect(url_for("leads.detail", lead_id=lead.id, organization_id=organization_id))
    except LeadServiceError as exc:
        db.session.rollback()
        _handle_service_error(exc)
        return render_template("leads/form.html", form=form, lead=None, organization_id=organization_id), 400


@leads_bp.route("/new")
def new_lead():
    organization_id = resolve_organization_id()
    form = LeadForm()
    _populate_lead_form_choices(form, organization_id)
    default_stage = get_default_stage(organization_id)
    form.stage_id.data = default_stage.id
    if not can_assign_to_others():
        form.assigned_to.data = current_user.id
    return render_template("leads/form.html", form=form, lead=None, organization_id=organization_id)


@leads_bp.route("/<int:lead_id>/edit")
def edit_lead(lead_id):
    organization_id = resolve_organization_id()
    try:
        lead = get_lead_for_org(lead_id, organization_id)
    except LeadServiceError:
        abort(404)
    if lead.status == "archived" and not can_archive_leads():
        abort(403)

    form = LeadForm()
    _populate_lead_form_choices(form, organization_id)
    _lead_to_form_data(form, lead)
    return render_template("leads/form.html", form=form, lead=lead, organization_id=organization_id)


@leads_bp.route("/<int:lead_id>", methods=["PUT", "POST"])
def update_lead(lead_id):
    organization_id = resolve_organization_id()
    if request.method == "PUT" or request.is_json:
        payload = request.get_json(silent=True) or {}
        data = payload
        if "assigned_to" in data:
            data["assigned_to"] = validate_assignment(data.get("assigned_to"))
        try:
            lead = LeadService.update(
                lead_id, data, organization_id, current_user.id, actor_role=current_user.role
            )
            db.session.commit()
            if request.is_json or request.method == "PUT":
                return json_success({"lead_id": lead.id})
            flash("Lead updated.", "success")
            return redirect(url_for("leads.detail", lead_id=lead.id))
        except LeadServiceError as exc:
            db.session.rollback()
            if request.is_json or request.method == "PUT":
                return json_error(exc.code, exc.message, 400)
            flash(exc.message, "danger")
            return redirect(url_for("leads.edit_lead", lead_id=lead_id))

    form = LeadForm()
    _populate_lead_form_choices(form, organization_id)
    if not form.validate_on_submit():
        try:
            lead = get_lead_for_org(lead_id, organization_id)
        except LeadServiceError:
            abort(404)
        return render_template("leads/form.html", form=form, lead=lead, organization_id=organization_id), 400

    data = _form_to_data(form)
    if not can_assign_to_others():
        data.pop("assigned_to", None)

    try:
        lead = LeadService.update(
            lead_id, data, organization_id, current_user.id, actor_role=current_user.role
        )
        db.session.commit()
        flash("Lead updated.", "success")
        return redirect(url_for("leads.detail", lead_id=lead.id, organization_id=organization_id))
    except LeadServiceError as exc:
        db.session.rollback()
        _handle_service_error(exc)
        lead = get_lead_for_org(lead_id, organization_id)
        return render_template("leads/form.html", form=form, lead=lead, organization_id=organization_id), 400


@leads_bp.route("/<int:lead_id>", methods=["DELETE", "POST"])
def delete_lead(lead_id):
    if request.method == "POST" and request.form.get("_method") != "DELETE":
        abort(405)
    if not can_archive_leads():
        return json_error("forbidden", "Insufficient permissions.", 403)

    organization_id = resolve_organization_id()
    try:
        LeadService.archive(lead_id, organization_id, current_user.id)
        db.session.commit()
        if request.is_json or request.method == "DELETE":
            return json_success({"lead_id": lead_id, "status": "archived"})
        flash("Lead archived.", "success")
        return redirect(url_for("leads.list_leads", organization_id=organization_id))
    except LeadServiceError as exc:
        db.session.rollback()
        return json_error(exc.code, exc.message, 400)


@leads_bp.route("/<int:lead_id>/stage", methods=["POST"])
def move_stage(lead_id):
    organization_id = resolve_organization_id()
    payload = request.get_json(silent=True) or {}
    stage_id = payload.get("stage_id") or request.form.get("stage_id", type=int)
    if not stage_id:
        return json_error("validation_error", "stage_id is required.", 400)

    try:
        lead = LeadService.move_stage(lead_id, int(stage_id), organization_id, current_user.id)
        db.session.commit()
        if wants_json_response() or request.is_json:
            return json_success({
                "lead_id": lead.id,
                "stage_id": lead.stage_id,
                "status": lead.status,
            })
        flash("Stage updated.", "success")
        return redirect(url_for("leads.detail", lead_id=lead.id, organization_id=organization_id))
    except LeadServiceError as exc:
        db.session.rollback()
        if wants_json_response() or request.is_json:
            return json_error(exc.code, exc.message, 400)
        flash(exc.message, "danger")
        return redirect(url_for("leads.detail", lead_id=lead_id, organization_id=organization_id))


@leads_bp.route("/<int:lead_id>/enrich", methods=["POST"])
def enrich_lead(lead_id):
    organization_id = resolve_organization_id()
    try:
        lead = get_lead_for_org(lead_id, organization_id)
    except LeadServiceError:
        abort(404)

    if lead.status == "archived" and not can_archive_leads():
        flash("Cannot enrich archived leads.", "danger")
        return redirect(
            url_for("leads.detail", lead_id=lead_id, organization_id=organization_id)
            if current_user.is_superadmin()
            else url_for("leads.detail", lead_id=lead_id)
        )

    if not current_app.config.get("AI_ENRICHMENT_ENABLED"):
        flash("AI enrichment is disabled.", "danger")
        return redirect(
            url_for("leads.detail", lead_id=lead_id, organization_id=organization_id)
            if current_user.is_superadmin()
            else url_for("leads.detail", lead_id=lead_id)
        )

    from app.ai.triggers import queue_manual_enrichment

    try:
        queue_manual_enrichment(lead)
        db.session.commit()
        flash("AI enrichment queued.", "success")
    except ValueError as exc:
        db.session.rollback()
        if str(exc) == "missing_fields":
            flash("Lead must have company, website, or LinkedIn URL.", "danger")
        elif str(exc) == "already_processing":
            flash("Enrichment is already in progress.", "info")
        else:
            flash("Could not queue enrichment.", "danger")
    return redirect(
        url_for("leads.detail", lead_id=lead_id, organization_id=organization_id)
        if current_user.is_superadmin()
        else url_for("leads.detail", lead_id=lead_id)
    )


@leads_bp.route("/<int:lead_id>/note", methods=["POST"])
def add_note(lead_id):
    organization_id = resolve_organization_id()
    content = request.form.get("content") or (request.get_json(silent=True) or {}).get("content")
    if lead_id and request.view_args:
        pass
    try:
        lead = get_lead_for_org(lead_id, organization_id)
        if lead.status == "archived" and not can_archive_leads():
            raise LeadServiceError("Cannot add notes to archived leads.", "archived")
        LeadService.add_note(lead_id, content or "", organization_id, current_user.id)
        db.session.commit()
        if request.is_json:
            return json_success({"lead_id": lead_id})
        flash("Note added.", "success")
        return redirect(url_for("leads.detail", lead_id=lead_id, organization_id=organization_id))
    except LeadServiceError as exc:
        db.session.rollback()
        if request.is_json:
            return json_error(exc.code, exc.message, 400)
        flash(exc.message, "danger")
        return redirect(url_for("leads.detail", lead_id=lead_id, organization_id=organization_id))


@leads_bp.route("/export")
def export_leads():
    organization_id = resolve_organization_id()
    filters = _filters_from_request()
    csv_data = LeadService.export_csv(organization_id, filters)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=flowleads-leads-{date_str}.csv"},
    )


@leads_bp.route("/bulk", methods=["POST"])
def bulk_action():
    organization_id = resolve_organization_id()
    action = request.form.get("action") or (request.get_json(silent=True) or {}).get("action")
    lead_ids_raw = request.form.get("lead_ids") or (request.get_json(silent=True) or {}).get("lead_ids")
    if isinstance(lead_ids_raw, str):
        try:
            lead_ids = json.loads(lead_ids_raw) if lead_ids_raw.startswith("[") else [int(x) for x in lead_ids_raw.split(",") if x.strip()]
        except (json.JSONDecodeError, ValueError):
            return json_error("validation_error", "Invalid lead IDs.", 400)
    elif isinstance(lead_ids_raw, list):
        lead_ids = [int(x) for x in lead_ids_raw]
    else:
        lead_ids = request.form.getlist("lead_ids", type=int)

    if action == "archive" and not can_archive_leads():
        return json_error("forbidden", "Insufficient permissions.", 403)
    if action == "assign" and not can_assign_to_others():
        return json_error("forbidden", "Insufficient permissions.", 403)

    payload = {}
    if action == "assign":
        payload["assigned_to"] = validate_assignment(
            request.form.get("assigned_to") or (request.get_json(silent=True) or {}).get("assigned_to")
        )
    if action == "change_stage":
        payload["stage_id"] = request.form.get("stage_id", type=int) or (request.get_json(silent=True) or {}).get("stage_id")

    try:
        result = LeadService.bulk_action(action, lead_ids, organization_id, current_user.id, payload)
        if action == "export":
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return Response(
                result["csv"],
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename=flowleads-leads-{date_str}.csv"},
            )
        db.session.commit()
        if request.is_json:
            return json_success(result)
        flash("Bulk action completed.", "success")
        return redirect(url_for("leads.list_leads", organization_id=organization_id))
    except LeadServiceError as exc:
        db.session.rollback()
        return json_error(exc.code, exc.message, 400)
