from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.core.permissions import require_2fa, require_role
from app.email.forms import ComposeEmailForm
from app.email.utils import sender_display_name
from app.email.services import (
    EmailService,
    EmailServiceError,
    TemplateService,
    email_sending_enabled,
    get_email_log_for_org,
    handle_mailgun_webhook,
    list_email_logs_for_lead,
    paginate_email_logs,
    send_test_email_to_user,
    verify_mailgun_webhook_signature,
)
from app.extensions import db
from app.leads.permissions import can_archive_leads, resolve_organization_id
from app.leads.services import LeadServiceError, get_lead_for_org
from app.users.models import Organization, User

email_bp = Blueprint("email", __name__)
webhooks_bp = Blueprint("webhooks", __name__, url_prefix="/api/webhooks")

UI_ROLES = ("superadmin", "admin", "user")


def _require_ui_role():
    if current_user.role not in UI_ROLES:
        abort(403)


def _check_lead_email_access(lead):
    if not lead.email:
        raise EmailServiceError("Lead has no email address.", "no_email")
    if lead.status == "archived" and not can_archive_leads():
        raise EmailServiceError("Cannot email archived leads.", "archived")


@email_bp.before_request
@login_required
def email_before_request():
    _require_ui_role()


@email_bp.route("/leads/<int:lead_id>/email/compose", methods=["GET"])
def compose(lead_id):
    organization_id = resolve_organization_id()
    try:
        lead = get_lead_for_org(lead_id, organization_id)
        _check_lead_email_access(lead)
    except LeadServiceError:
        abort(404)
    except EmailServiceError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("leads.detail", lead_id=lead_id, organization_id=organization_id))

    templates = TemplateService.list_for_organization(organization_id)
    form = ComposeEmailForm()
    form.template_id.choices = [(0, "— No template —")] + [(t.id, t.name) for t in templates]

    selected_id = request.args.get("template_id", type=int) or 0
    preview_subject = ""
    preview_html = ""
    if selected_id:
        try:
            template = TemplateService.get_template(selected_id, organization_id)
            rendered = TemplateService.render_for_lead(template, lead, sender_display_name())
            preview_subject = rendered["subject"]
            preview_html = rendered["body_html"]
            form.subject.data = preview_subject
        except EmailServiceError:
            pass

    return render_template(
        "email/compose.html",
        lead=lead,
        form=form,
        templates=templates,
        organization_id=organization_id,
        email_enabled=email_sending_enabled(),
        ai_summary=lead.ai_summary,
        preview_subject=preview_subject,
        preview_html=preview_html,
        sender_name=sender_display_name(),
    )


@email_bp.route("/leads/<int:lead_id>/email/send", methods=["POST"])
def send(lead_id):
    organization_id = resolve_organization_id()
    form = ComposeEmailForm()
    templates = TemplateService.list_for_organization(organization_id)
    form.template_id.choices = [(0, "— No template —")] + [(t.id, t.name) for t in templates]
    try:
        lead = get_lead_for_org(lead_id, organization_id)
        _check_lead_email_access(lead)
    except LeadServiceError:
        abort(404)
    except EmailServiceError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("leads.detail", lead_id=lead_id, organization_id=organization_id))

    if not form.validate_on_submit():
        flash("Invalid form submission.", "danger")
        return redirect(url_for("email.compose", lead_id=lead_id, organization_id=organization_id))

    if not email_sending_enabled():
        flash("Email sending is disabled.", "danger")
        return redirect(url_for("email.compose", lead_id=lead_id, organization_id=organization_id))

    try:
        result = EmailService.send_to_lead(
            lead_id,
            current_user.id,
            form.subject.data,
            form.body_html.data,
            None,
            organization_id=organization_id,
            actor=current_user,
        )
        db.session.commit()
        if result.get("success"):
            flash("Email sent successfully.", "success")
            return redirect(url_for("email.history", lead_id=lead_id, organization_id=organization_id))
        flash(result.get("error") or "Failed to send email.", "danger")
    except EmailServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    except Exception:
        db.session.rollback()
        flash("An unexpected error occurred while sending email.", "danger")

    return redirect(url_for("email.compose", lead_id=lead_id, organization_id=organization_id))


@email_bp.route("/leads/<int:lead_id>/email/history", methods=["GET"])
def history(lead_id):
    organization_id = resolve_organization_id()
    try:
        lead = get_lead_for_org(lead_id, organization_id)
    except LeadServiceError:
        abort(404)

    logs = list_email_logs_for_lead(lead_id, organization_id)
    user_ids = {log.user_id for log in logs if log.user_id}
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}

    return render_template(
        "email/history.html",
        lead=lead,
        logs=logs,
        users=users,
        organization_id=organization_id,
    )


@email_bp.route("/leads/<int:lead_id>/email/history/<int:email_log_id>", methods=["GET"])
def history_detail(lead_id, email_log_id):
    organization_id = resolve_organization_id()
    try:
        lead = get_lead_for_org(lead_id, organization_id)
        log = get_email_log_for_org(email_log_id, organization_id)
        if log.lead_id != lead.id or log.organization_id != organization_id:
            abort(404)
    except (LeadServiceError, EmailServiceError):
        abort(404)

    sender = db.session.get(User, log.user_id) if log.user_id else None
    return render_template(
        "email/history_detail.html",
        lead=lead,
        log=log,
        sender=sender,
        organization_id=organization_id,
    )


@email_bp.route("/admin/email/logs", methods=["GET"])
@login_required
@require_role("superadmin")
@require_2fa
def admin_email_logs():
    page = request.args.get("page", 1, type=int)
    org_filter = request.args.get("organization_id", type=int)
    pagination = paginate_email_logs(page=page, per_page=25, organization_id=org_filter)
    organizations = Organization.query.order_by(Organization.name).all()
    return render_template(
        "admin/email_logs.html",
        pagination=pagination,
        logs=pagination.items,
        organizations=organizations,
        org_filter=org_filter,
    )


@email_bp.route("/admin/email/test", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def admin_email_test():
    try:
        result = send_test_email_to_user(current_user)
        db.session.commit()
        if result.get("success"):
            flash("Test email sent.", "success")
        else:
            flash(result.get("error") or "Test email failed.", "danger")
    except EmailServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("email.admin_email_logs"))


@webhooks_bp.route("/mailgun", methods=["POST"])
def mailgun_webhook():
    if not current_app.config.get("MAILGUN_WEBHOOK_SIGNING_KEY"):
        abort(503)

    timestamp = request.form.get("timestamp")
    token = request.form.get("token")
    signature = request.form.get("signature")
    if not timestamp or not token or not signature:
        abort(403)
    if not verify_mailgun_webhook_signature(timestamp, token, signature):
        abort(403)

    import json as json_mod

    event_data_raw = request.form.get("event-data")
    payload = {}
    if event_data_raw:
        try:
            payload = json_mod.loads(event_data_raw)
        except json_mod.JSONDecodeError:
            abort(400)
    else:
        payload = {"event": request.form.get("event")}
        if request.form.get("message-id"):
            payload["message-id"] = request.form.get("message-id")

    handle_mailgun_webhook(payload)
    db.session.commit()
    return "", 200
