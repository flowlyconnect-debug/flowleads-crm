import json
from datetime import datetime, timedelta, timezone

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
from app.core.permissions import require_role
from app.extensions import cache, db
from app.leads.forms import BulkActionForm, LeadFilterForm, LeadForm, QuickNoteForm
from app.leads.models import Activity, Lead, PipelineStage
from app.leads.permissions import (
    can_archive_leads,
    can_assign_to_others,
    resolve_organization_id,
    validate_assignment,
)
from app.leads.services import LeadService, LeadServiceError, get_default_stage, get_lead_for_org
from app.tasks.forms import TaskForm
from app.tasks.services import TaskService, TaskServiceError
from app.users.models import User

from app import stage_label_fi  # noqa: E402

leads_bp = Blueprint("leads", __name__, url_prefix="/leads")

UI_ROLES = ("superadmin", "admin", "user")


def _require_ui_role():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in UI_ROLES:
        abort(403)


def _filters_from_request() -> dict:
    sort_dir = request.args.get("dir")
    if not sort_dir:
        sort_dir = request.args.get("order")
    return {
        "search": request.args.get("search", ""),
        "stage_id": request.args.get("stage_id", type=int) or None,
        "source": request.args.get("source") or None,
        "assigned_to": request.args.get("assigned_to", type=int) or None,
        "status": request.args.get("status") or None,
        "score_min": request.args.get("score_min", type=int),
        "score_max": request.args.get("score_max", type=int),
        "created_from": _parse_date(request.args.get("created_from")),
        "created_to": _parse_date(request.args.get("created_to"), end_of_day=True),
        "sort": request.args.get("sort", "created_at"),
        "dir": sort_dir or "desc",
        "no_contact_7": request.args.get("no_contact_7") == "1",
        "gdpr_consent": request.args.get("gdpr_consent") == "1",
        "marketing_opt_in": request.args.get("marketing_opt_in") == "1",
        "unsubscribed": request.args.get("unsubscribed") == "1",
        "is_anonymized": request.args.get("is_anonymized") == "1",
        # target details of catalogue leads (quick filter bar)
        "renovation": (request.args.get("renovation") or "").strip() or None,
        "city": (request.args.get("city") or "").strip() or None,
        "region": (request.args.get("region") or "").strip() or None,
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


def _ensure_utc(value: datetime | None) -> datetime | None:
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _activity_icon(activity_type: str | None) -> str:
    icon_map = {
        "note": "📝",
        "email_sent": "✉️",
        "email_opened": "👀",
        "email_clicked": "🔗",
        "call": "📞",
        "stage_changed": "🔁",
        "task_created": "📋",
        "task_completed": "✅",
        "meeting_scheduled": "📅",
        "proposal_sent": "📄",
        "proposal_viewed": "👁️",
        "proposal_accepted": "🎉",
    }
    return icon_map.get((activity_type or "").strip(), "•")


def _activity_label(activity: Activity) -> str:
    fallback = (activity.type or "aktiviteetti").replace("_", " ").strip().capitalize()
    return (activity.content or "").strip() or fallback


def _time_ago_label(created_at: datetime | None) -> str:
    ts = _ensure_utc(created_at)
    if not ts:
        return "—"
    now = datetime.now(timezone.utc)
    delta = now - ts
    minutes = int(max(delta.total_seconds(), 0) // 60)
    if minutes < 1:
        return "Juuri nyt"
    if minutes < 60:
        return f"{minutes} min sitten"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h sitten"
    days = hours // 24
    if days < 7:
        return f"{days} pv sitten"
    return ts.strftime("%d.%m.%Y")


def _engagement_level_from_last_activity(last_activity: Activity | None) -> int:
    if not last_activity or not last_activity.created_at:
        return 0
    days_ago = (datetime.now(timezone.utc) - _ensure_utc(last_activity.created_at)).days
    if days_ago <= 3:
        return 3
    if days_ago <= 14:
        return 2
    return 1


def _last_activity_label(activity_type: str | None) -> str:
    mapping = {
        "email": "Sähköposti",
        "email_sent": "Sähköposti",
        "call": "Soitettu",
        "note": "Muistiinpano",
        "meeting": "Tapaaminen",
        "meeting_scheduled": "Tapaaminen",
        "stage_change": "Vaihe muuttunut",
        "stage_changed": "Vaihe muuttunut",
        "ai_score": "AI pisteytys",
        "ai_enriched": "AI pisteytys",
    }
    return mapping.get((activity_type or "").strip(), "Aktiviteetti")


def _populate_lead_form_choices(form: LeadForm, organization_id: int):
    stages = PipelineStage.query.filter_by(organization_id=organization_id).order_by(
        PipelineStage.order_index
    ).all()
    form.stage_id.choices = [(s.id, s.name) for s in stages]
    users = _org_users(organization_id)
    form.assigned_to.choices = [(0, "Ei vastuuhenkilöä")] + [(u.id, u.email) for u in users]


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
    form.deal_value.data = lead.deal_value
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
        "deal_value": form.deal_value.data,
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


@leads_bp.route("", methods=["GET"])
@leads_bp.route("/", methods=["GET"])
def list_leads():
    organization_id = resolve_organization_id()
    filters = _filters_from_request()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    pagination = LeadService.search(organization_id, filters, page=page, per_page=25)
    from app.analytics.currency import currency_symbol, get_default_currency

    org_currency = get_default_currency(organization_id)
    form_args = request.args.copy()
    if "dir" not in form_args and "order" in form_args:
        form_args["dir"] = form_args.get("order")
    for key in ("organization_id", "page"):
        form_args.pop(key, None)
    filter_form = LeadFilterForm(form_args, meta={"csrf": False})
    stages = PipelineStage.query.filter_by(organization_id=organization_id).order_by(
        PipelineStage.order_index
    ).all()
    filter_form.stage_id.choices = [(0, "Kaikki vaiheet")] + [(s.id, stage_label_fi(s.name)) for s in stages]
    filter_form.stage_id.coerce = lambda v: int(v) if v not in (None, "", 0) else 0
    users = _org_users(organization_id)
    filter_form.assigned_to.choices = [(0, "Kuka tahansa")] + [(u.id, u.email) for u in users]
    filter_form.assigned_to.coerce = lambda v: int(v) if v not in (None, "", 0) else 0

    bulk_form = BulkActionForm()
    bulk_form.stage_id.choices = [(s.id, s.name) for s in stages]
    bulk_form.assigned_to.choices = [(u.id, u.email) for u in users]
    now = datetime.now(timezone.utc)

    return render_template(
        "leads/list.html",
        pagination=pagination,
        leads=pagination.items,
        filter_form=filter_form,
        filters=filters,
        quick_filter_options=LeadService.quick_filter_options(organization_id),
        bulk_form=bulk_form,
        organization_id=organization_id,
        can_archive=can_archive_leads(),
        can_assign_others=can_assign_to_others(),
        org_currency=org_currency,
        currency_symbol=currency_symbol(org_currency),
        now=now,
        last_activity_label=_last_activity_label,
    )


@leads_bp.route("/pipeline")
def pipeline():
    organization_id = resolve_organization_id()
    filters = {
        "assigned_to": request.args.get("assigned_to", type=int),
        "source": request.args.get("source"),
        "score_min": request.args.get("score_min", type=int),
        "score_max": request.args.get("score_max", type=int),
        "created_from": _parse_date(request.args.get("created_from")),
        "created_to": _parse_date(request.args.get("created_to"), end_of_day=True),
    }
    # Not cached: a cached board showed a moved card in its old column on reload.
    data = LeadService.get_pipeline_data(organization_id, filters)
    users = _org_users(organization_id)
    from app.analytics.currency import currency_symbol, get_default_currency

    return render_template(
        "leads/pipeline.html",
        stages=data["stages"],
        leads_by_stage=data["leads_by_stage"],
        stage_deal_totals=data.get("stage_deal_totals", {}),
        users=users,
        filters=filters,
        organization_id=organization_id,
        csrf_token=generate_csrf,
        currency_symbol=currency_symbol(get_default_currency(organization_id)),
        now=datetime.now(timezone.utc),
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
    lead_tasks = TaskService.get_for_lead(lead.id, organization_id)
    task_form = TaskForm()
    task_form.assigned_to.choices = [(u.id, u.email) for u in users]
    if not can_assign_to_others():
        task_form.assigned_to.data = current_user.id
    else:
        task_form.assigned_to.data = lead.assigned_to or current_user.id
    default_due = datetime.now(timezone.utc) + timedelta(days=1)
    task_form.due_date.data = default_due.replace(tzinfo=None)
    from app.sequences.ui_helpers import (
        available_sequences_for_enroll,
        lead_enrollment_rows,
    )

    sequence_enrollments = lead_enrollment_rows(lead.id, organization_id)
    enrollable_sequences = available_sequences_for_enroll(organization_id)
    from app.gdpr.forms import AnonymizeLeadForm

    anonymize_form = AnonymizeLeadForm()
    from app.calendar.forms import ScheduleMeetingForm
    from app.calendar.services import CalendarService

    schedule_meeting_form = ScheduleMeetingForm()
    default_title = f"Tapaaminen — {lead.company or lead.display_name}"
    schedule_meeting_form.title.data = default_title
    if lead.email:
        schedule_meeting_form.attendees.data = lead.email
    if lead.ai_summary:
        schedule_meeting_form.description.data = lead.ai_summary
    default_start = datetime.now(timezone.utc) + timedelta(hours=1)
    schedule_meeting_form.start_at.data = default_start.replace(tzinfo=None)
    calendar_connection = CalendarService.get_active_connection(
        current_user.id, organization_id
    )
    lead_meetings = CalendarService.get_events_for_lead(lead.id, organization_id)
    from app.proposals.services import ProposalService

    lead_proposals_summary = ProposalService.get_lead_proposals_summary(lead.id, organization_id)
    org_query = (
        {"organization_id": organization_id}
        if current_user.is_superadmin()
        else {}
    )
    from app.analytics.currency import currency_symbol, get_default_currency
    from app.analytics.prediction import PredictionService

    latest_prediction = PredictionService.get_latest_prediction(lead.id, organization_id)
    org_currency = get_default_currency(organization_id)
    from app.leads.playbook import get_playbook_data
    from app.leads.property_info import get_property_info_rows

    playbook = get_playbook_data(lead, organization_id, current_user)
    property_info_rows = get_property_info_rows(lead.ai_contact_info)
    # links from the lead to its housing-company card and property manager
    meta = lead.ai_contact_info if isinstance(lead.ai_contact_info, dict) else {}
    target_listing_id = None
    try:
        listing_id = int(meta.get("listing_id")) if meta.get("listing_id") else None
    except (TypeError, ValueError):
        listing_id = None
    if listing_id:
        from app.listings.models import ListingMatch

        if ListingMatch.query.filter_by(
            organization_id=organization_id, listing_id=listing_id, lead_id=lead.id
        ).first():
            target_listing_id = listing_id
    manager_name = str(meta.get("isannoitsija_yritys") or "").strip()
    # the housing company's renovation timeline and why the target was found
    from app.listings.pts import pts_from_text, pts_strip

    lead_pts, lead_evidence = None, None
    if target_listing_id:
        from app.extensions import db
        from app.listings.models import Listing

        listing = db.session.get(Listing, target_listing_id)
        if listing is not None and listing.renovations:
            lead_pts = pts_strip(listing.renovations)
            best = max(listing.renovations, key=lambda r: r.confidence)
            if best.evidence:
                lead_evidence = {"text": best.evidence, "renovation": best, "listing": listing}
    if lead_pts is None and meta.get("remonttityyppi"):
        lead_pts = pts_from_text(meta.get("remonttityyppi"), meta.get("remontin_ajankohta"))
    return render_template(
        "leads/detail.html",
        lead_pts=lead_pts,
        lead_evidence=lead_evidence,
        lead=lead,
        playbook=playbook,
        property_info_rows=property_info_rows,
        target_listing_id=target_listing_id,
        manager_name=manager_name,
        activities=activities,
        stages=stages,
        users=users,
        note_form=note_form,
        lead_tasks=lead_tasks,
        task_form=task_form,
        organization_id=organization_id,
        org_query=org_query,
        can_archive=can_archive_leads(),
        can_assign_others=can_assign_to_others(),
        sequence_enrollments=sequence_enrollments,
        enrollable_sequences=enrollable_sequences,
        anonymize_form=anonymize_form,
        can_gdpr_admin=current_user.role in ("admin", "superadmin"),
        schedule_meeting_form=schedule_meeting_form,
        calendar_connection=calendar_connection,
        lead_meetings_upcoming=lead_meetings["upcoming"],
        lead_meetings_past=lead_meetings["past"],
        lead_proposals=lead_proposals_summary["proposals"],
        lead_proposals_accepted_total=lead_proposals_summary["accepted_total"],
        latest_prediction=latest_prediction,
        org_currency=org_currency,
        currency_symbol=currency_symbol(org_currency),
    )


@leads_bp.route("", methods=["POST"])
def create_lead():
    organization_id = resolve_organization_id()
    form = LeadForm()
    _populate_lead_form_choices(form, organization_id)
    if not form.validate_on_submit():
        from app.analytics.currency import get_default_currency

        return render_template(
            "leads/form.html",
            form=form,
            lead=None,
            organization_id=organization_id,
            org_currency=get_default_currency(organization_id),
        ), 400

    data = _form_to_data(form)
    if not can_assign_to_others() and not data.get("assigned_to"):
        data["assigned_to"] = current_user.id

    duplicate = _find_duplicate_lead(organization_id, data)
    if duplicate is not None:
        from app.analytics.currency import get_default_currency

        flash(
            "Sama liidi on jo olemassa (sama sähköposti ja yritys). Avaa se Liidit-listalta haulla.",
            "warning",
        )
        return render_template(
            "leads/form.html",
            form=form,
            lead=None,
            organization_id=organization_id,
            org_currency=get_default_currency(organization_id),
            duplicate_lead=duplicate,
        ), 400

    try:
        lead = LeadService.create(
            data, organization_id, current_user.id, actor_role=current_user.role
        )
        db.session.commit()
        flash("Liidi luotu.", "success")
        return redirect(url_for("leads.detail", lead_id=lead.id, **_org_kwargs(organization_id)))
    except LeadServiceError as exc:
        db.session.rollback()
        _handle_service_error(exc)
        from app.analytics.currency import get_default_currency

        return render_template(
            "leads/form.html",
            form=form,
            lead=None,
            organization_id=organization_id,
            org_currency=get_default_currency(organization_id),
        ), 400


def _find_duplicate_lead(organization_id: int, data: dict):
    """An open lead with the same email AND the same company. Email alone is
    not enough: one property manager is the contact of many housing companies."""
    from sqlalchemy import func

    email = (data.get("email") or "").strip().lower()
    company = (data.get("company") or "").strip().lower()
    if not email or not company:
        return None
    return Lead.query.filter(
        Lead.organization_id == organization_id,
        Lead.status != "archived",
        func.lower(Lead.email) == email,
        func.lower(func.trim(Lead.company)) == company,
    ).first()


@leads_bp.route("/new")
def new_lead():
    organization_id = resolve_organization_id()
    form = LeadForm()
    _populate_lead_form_choices(form, organization_id)
    default_stage = get_default_stage(organization_id)
    form.stage_id.data = default_stage.id
    if not can_assign_to_others():
        form.assigned_to.data = current_user.id
    from app.analytics.currency import get_default_currency

    return render_template(
        "leads/form.html",
        form=form,
        lead=None,
        organization_id=organization_id,
        org_currency=get_default_currency(organization_id),
    )


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
    from app.analytics.currency import get_default_currency

    return render_template(
        "leads/form.html",
        form=form,
        lead=lead,
        organization_id=organization_id,
        org_currency=get_default_currency(organization_id),
    )


def _org_kwargs(organization_id) -> dict:
    """organization_id belongs in the URL only for a superadmin."""
    return {"organization_id": organization_id} if current_user.is_superadmin() else {}


@leads_bp.route("/<int:lead_id>", methods=["PUT", "POST"])
def update_lead(lead_id):
    # The "Arkistoi" form posts here with _method=DELETE (HTML forms cannot
    # send DELETE, and this rule wins over delete_lead's POST).
    if request.method == "POST" and request.form.get("_method") == "DELETE":
        return delete_lead(lead_id)
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
            flash("Liidi tallennettu.", "success")
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
        flash("Liidi tallennettu.", "success")
        return redirect(url_for("leads.detail", lead_id=lead.id, **_org_kwargs(organization_id)))
    except LeadServiceError as exc:
        db.session.rollback()
        _handle_service_error(exc)
        lead = get_lead_for_org(lead_id, organization_id)
        return render_template("leads/form.html", form=form, lead=lead, organization_id=organization_id), 400


@leads_bp.route("/<int:lead_id>", methods=["DELETE"])
def delete_lead(lead_id):
    if not can_archive_leads():
        return json_error("forbidden", "Insufficient permissions.", 403)

    organization_id = resolve_organization_id()
    try:
        LeadService.archive(lead_id, organization_id, current_user.id)
        db.session.commit()
        if request.is_json or request.method == "DELETE":
            return json_success({"lead_id": lead_id, "status": "archived"})
        flash("Liidi arkistoitu.", "success")
        return redirect(url_for("leads.list_leads", **_org_kwargs(organization_id)))
    except LeadServiceError as exc:
        db.session.rollback()
        if request.is_json or request.method == "DELETE":
            return json_error(exc.code, exc.message, 400)
        flash(exc.message, "danger")
        return redirect(url_for("leads.detail", lead_id=lead_id, **_org_kwargs(organization_id)))


@leads_bp.route("/<int:lead_id>/restore", methods=["POST"])
def restore_lead(lead_id):
    """Un-archive, or reopen a lost lead."""
    organization_id = resolve_organization_id()
    try:
        LeadService.restore(lead_id, organization_id, current_user.id)
        db.session.commit()
        flash("Liidi palautettu avoimeksi.", "success")
    except LeadServiceError as exc:
        db.session.rollback()
        if exc.code == "not_found":
            abort(404)
        flash(exc.message, "danger")
    return redirect(url_for("leads.detail", lead_id=lead_id, **_org_kwargs(organization_id)))


@leads_bp.route("/<int:lead_id>/log-contact", methods=["POST"])
def log_contact(lead_id):
    """"Kirjaa soitto" / "Kirjaa sähköposti" on the lead page."""
    organization_id = resolve_organization_id()
    try:
        LeadService.log_contact(
            lead_id,
            organization_id,
            current_user.id,
            request.form.get("kind") or "",
            request.form.get("note"),
        )
        db.session.commit()
        flash("Yhteydenotto kirjattu.", "success")
    except LeadServiceError as exc:
        db.session.rollback()
        if exc.code == "not_found":
            abort(404)
        flash(exc.message, "danger")
    return redirect(url_for("leads.detail", lead_id=lead_id, **_org_kwargs(organization_id)))


@leads_bp.route("/<int:lead_id>/stage", methods=["POST", "PATCH"])
def move_stage(lead_id):
    organization_id = resolve_organization_id()
    payload = request.get_json(silent=True) or {}
    stage_id = payload.get("stage_id") or request.form.get("stage_id", type=int)
    try:
        stage_id = int(stage_id) if stage_id else None
    except (TypeError, ValueError):
        stage_id = None
    if not stage_id:
        return json_error("validation_error", "stage_id is required.", 400)

    lost_reason = payload.get("lost_reason") or request.form.get("lost_reason")
    lost_reason_note = payload.get("lost_reason_note") or request.form.get("lost_reason_note")
    try:
        lead = LeadService.move_stage(
            lead_id,
            int(stage_id),
            organization_id,
            current_user.id,
            lost_reason=lost_reason,
            lost_reason_note=lost_reason_note,
        )
        db.session.commit()
        if wants_json_response() or request.is_json:
            stage_name = lead.stage.name if lead.stage else ""
            return json_success({
                "stage": stage_name,
                "lead_id": lead.id,
                "stage_id": lead.stage_id,
                "status": lead.status,
            })
        flash("Vaihe päivitetty.", "success")
        return redirect(url_for("leads.detail", lead_id=lead.id, **_org_kwargs(organization_id)))
    except LeadServiceError as exc:
        db.session.rollback()
        if wants_json_response() or request.is_json:
            return json_error(exc.code, exc.message, 400)
        flash(exc.message, "danger")
        return redirect(url_for("leads.detail", lead_id=lead_id, **_org_kwargs(organization_id)))


@leads_bp.route("/<int:lead_id>/predict", methods=["POST"])
@require_role("superadmin", "admin", "user")
def predict_lead_route(lead_id):
    organization_id = resolve_organization_id()
    try:
        get_lead_for_org(lead_id, organization_id)
    except LeadServiceError:
        abort(404)

    from app.analytics.prediction import PredictionService, PredictionServiceError

    try:
        probability = PredictionService.predict_lead_for_org(lead_id, organization_id)
        db.session.commit()
        flash(f"Ennuste päivitetty: {probability * 100:.0f}%", "success")
    except PredictionServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    except Exception:
        db.session.rollback()
        flash("Ennusteen päivitys epäonnistui.", "danger")

    return redirect(
        url_for("leads.detail", lead_id=lead_id, **_org_kwargs(organization_id))
        if current_user.is_superadmin()
        else url_for("leads.detail", lead_id=lead_id)
    )


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
            url_for("leads.detail", lead_id=lead_id, **_org_kwargs(organization_id))
            if current_user.is_superadmin()
            else url_for("leads.detail", lead_id=lead_id)
        )

    if not current_app.config.get("AI_ENRICHMENT_ENABLED"):
        flash("AI enrichment is disabled.", "danger")
        return redirect(
            url_for("leads.detail", lead_id=lead_id, **_org_kwargs(organization_id))
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
        url_for("leads.detail", lead_id=lead_id, **_org_kwargs(organization_id))
        if current_user.is_superadmin()
        else url_for("leads.detail", lead_id=lead_id)
    )


@leads_bp.route("/<int:lead_id>/tasks", methods=["GET"])
def lead_tasks(lead_id):
    organization_id = resolve_organization_id()
    try:
        get_lead_for_org(lead_id, organization_id)
    except LeadServiceError:
        abort(404)
    tasks = TaskService.get_for_lead(lead_id, organization_id)
    if request.is_json or wants_json_response():
        return json_success(
            {
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "status": t.status,
                        "type": t.type,
                        "priority": t.priority,
                        "due_date": t.due_date.isoformat() if t.due_date else None,
                        "is_overdue": t.is_overdue,
                    }
                    for t in tasks
                ]
            }
        )
    return redirect(
        url_for(
            "leads.detail",
            lead_id=lead_id,
            organization_id=organization_id if current_user.is_superadmin() else None,
        )
    )


@leads_bp.route("/<int:lead_id>/tasks", methods=["POST"])
def create_lead_task(lead_id):
    organization_id = resolve_organization_id()
    try:
        get_lead_for_org(lead_id, organization_id)
    except LeadServiceError:
        abort(404)

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        try:
            task = TaskService.create(
                payload,
                organization_id,
                current_user.id,
                lead_id=lead_id,
                actor_role=current_user.role,
            )
            db.session.commit()
            return json_success({"task_id": task.id}, status=201)
        except TaskServiceError as exc:
            db.session.rollback()
            return json_error(exc.code, exc.message, 400)

    form = TaskForm()
    users = _org_users(organization_id)
    form.assigned_to.choices = [(u.id, u.email) for u in users]
    if not form.validate_on_submit():
        flash("Invalid task data.", "danger")
        return redirect(
            url_for(
                "leads.detail",
                lead_id=lead_id,
                organization_id=organization_id if current_user.is_superadmin() else None,
            )
        )

    try:
        TaskService.create(
            form.to_service_data(),
            organization_id,
            current_user.id,
            lead_id=lead_id,
            actor_role=current_user.role,
        )
        db.session.commit()
        flash("Task created.", "success")
    except TaskServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")

    return redirect(
        url_for(
            "leads.detail",
            lead_id=lead_id,
            organization_id=organization_id if current_user.is_superadmin() else None,
        )
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
        flash("Muistiinpano lisätty.", "success")
        return redirect(url_for("leads.detail", lead_id=lead_id, **_org_kwargs(organization_id)))
    except LeadServiceError as exc:
        db.session.rollback()
        if request.is_json:
            return json_error(exc.code, exc.message, 400)
        flash(exc.message, "danger")
        return redirect(url_for("leads.detail", lead_id=lead_id, **_org_kwargs(organization_id)))


@leads_bp.route("/import", methods=["GET", "POST"])
def import_leads_route():
    """The customer's own lead list (CSV) into the CRM."""
    from app.leads.import_services import LeadImportError, import_leads, parse_lead_csv

    organization_id = resolve_organization_id()
    org_kwargs = _org_kwargs(organization_id)
    result = None
    if request.method == "POST":
        upload = request.files.get("file")
        if upload is None or not (upload.filename or "").strip():
            flash("Valitse CSV-tiedosto.", "warning")
            return redirect(url_for("leads.import_leads_route", **org_kwargs))
        try:
            rows = parse_lead_csv(upload.read())
            result = import_leads(
                rows,
                organization_id,
                current_user.id,
                actor_role=current_user.role,
                assigned_to=current_user.id if current_user.role == "user" else None,
            )
            from app.core.audit import log_audit

            log_audit(
                "leads_imported",
                user_id=current_user.id,
                organization_id=organization_id,
                target_type="lead",
                target_id=None,
                metadata={"created": result["created"], "skipped": result["skipped"], "errors": len(result["errors"])},
            )
            db.session.commit()
        except LeadImportError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
            return redirect(url_for("leads.import_leads_route", **org_kwargs))
    from app.leads.import_services import CSV_TEMPLATE_COLUMNS

    return render_template(
        "leads/import.html",
        result=result,
        columns=CSV_TEMPLATE_COLUMNS,
        organization_id=organization_id,
        org_query=org_kwargs,
    )


@leads_bp.route("/import-template.csv")
def import_leads_template():
    from app.leads.import_services import csv_template

    return Response(
        csv_template(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=flowleads-liidit-malli.csv"},
    )


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
        try:
            lead_ids = [int(x) for x in lead_ids_raw]
        except (TypeError, ValueError):
            return json_error("validation_error", "Invalid lead IDs.", 400)
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
        # moving to Hävitty needs a reason; the bulk bar has no picker for it
        payload["lost_reason"] = (
            request.form.get("lost_reason") or (request.get_json(silent=True) or {}).get("lost_reason") or "other"
        )

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
        flash("Massatoiminto suoritettu.", "success")
        return redirect(url_for("leads.list_leads", **_org_kwargs(organization_id)))
    except (LeadServiceError, ValueError, TypeError) as exc:
        db.session.rollback()
        message = getattr(exc, "message", None) or "Virheellinen pyyntö."
        if request.is_json:
            return json_error(getattr(exc, "code", "validation_error"), message, 400)
        flash(message, "danger")
        return redirect(url_for("leads.list_leads", **_org_kwargs(organization_id)))


from app.gdpr.routes import register_lead_gdpr_routes  # noqa: E402

register_lead_gdpr_routes(leads_bp)

from app.calendar.routes import register_calendar_lead_routes  # noqa: E402

register_calendar_lead_routes(leads_bp)

from app.proposals.routes import register_proposal_lead_routes  # noqa: E402

register_proposal_lead_routes(leads_bp)
