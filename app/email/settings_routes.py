from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.permissions import require_role
from app.email.forms import EmailTemplateForm, OrganizationEmailSettingsForm
from app.email.utils import sender_display_name
from app.email.seed import seed_system_email_templates
from app.email.services import (
    EmailServiceError,
    TemplateService,
    check_organization_domain_status,
    register_organization_domain,
    update_organization_email_settings,
)
from app.extensions import db
from app.leads.models import Lead
from app.settings.routes import settings_bp
from app.users.models import Organization


def _settings_org_id(require_admin: bool = False) -> int:
    if current_user.is_superadmin():
        from app.core.tenant import resolve_organization_id

        return resolve_organization_id()
    if current_user.organization_id is None:
        abort(403)
    return current_user.organization_id


@settings_bp.route("/email", methods=["GET", "POST"])
@login_required
@require_role("admin", "superadmin")
def email_settings():
    org_id = _settings_org_id()

    org = db.session.get(Organization, org_id)
    if org is None:
        abort(404)
    form = OrganizationEmailSettingsForm(obj=org)
    if form.validate_on_submit():
        try:
            update_organization_email_settings(
                org,
                email_from_name=form.email_from_name.data,
                email_from_email=form.email_from_email.data,
                mailgun_domain=form.mailgun_domain.data,
                user_id=current_user.id,
            )
            db.session.commit()
            flash("Email settings saved.", "success")
        except EmailServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
        return redirect(url_for("settings.email_settings", organization_id=org_id))

    dns_records = request.args.get("_show_dns") and _consume_dns_records(org_id)
    return render_template(
        "settings/email.html",
        form=form,
        organization=org,
        organization_id=org_id,
        dns_records=dns_records,
    )


_DNS_RECORDS_SESSION = "pending_mailgun_dns_records"


def _store_dns_records(org_id: int, records: list[dict]) -> None:
    from flask import session

    pending = session.get(_DNS_RECORDS_SESSION, {})
    pending[str(org_id)] = records
    session[_DNS_RECORDS_SESSION] = pending
    session.modified = True


def _consume_dns_records(org_id: int) -> list[dict] | None:
    from flask import session

    pending = session.get(_DNS_RECORDS_SESSION, {})
    records = pending.pop(str(org_id), None)
    session[_DNS_RECORDS_SESSION] = pending
    session.modified = True
    return records


@settings_bp.route("/email/domain/register", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
def email_domain_register():
    org_id = _settings_org_id(require_admin=True)
    org = db.session.get(Organization, org_id)
    try:
        records = register_organization_domain(org)
        db.session.commit()
        _store_dns_records(org_id, records)
        flash(
            "Verkkotunnus rekisteröity Mailguniin. Lisää alla näkyvät DNS-tietueet "
            "verkkotunnuksesi DNS-hallinnassa, sitten paina “Tarkista tila”.",
            "success",
        )
    except EmailServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("settings.email_settings", organization_id=org_id, _show_dns=1))


@settings_bp.route("/email/domain/verify", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
def email_domain_verify():
    org_id = _settings_org_id(require_admin=True)
    org = db.session.get(Organization, org_id)
    try:
        records = check_organization_domain_status(org)
        db.session.commit()
        _store_dns_records(org_id, records)
        if org.mailgun_domain_verified_at:
            flash("Verkkotunnus on vahvistettu — myyntisähköposti lähtee nyt tältä domainilta.", "success")
        else:
            flash("Verkkotunnus ei ole vielä vahvistettu. DNS-muutokset voivat kestää hetken.", "warning")
    except EmailServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("settings.email_settings", organization_id=org_id, _show_dns=1))


@settings_bp.route("/email-templates", methods=["GET"])
@login_required
@require_role("admin", "superadmin", "user")
def email_templates_list():
    org_id = _settings_org_id()
    seed_system_email_templates(commit=True)
    templates = TemplateService.list_for_organization(org_id)
    can_edit = current_user.role in ("admin", "superadmin")
    return render_template(
        "settings/email_templates.html",
        templates=templates,
        organization_id=org_id,
        can_edit=can_edit,
    )


def _create_email_template(org_id: int):
    form = EmailTemplateForm()
    if form.validate_on_submit():
        try:
            TemplateService.create_template(
                org_id,
                current_user.id,
                name=form.name.data,
                subject_template=form.subject_template.data,
                body_html_template=form.body_html_template.data,
                body_text_template=form.body_text_template.data,
            )
            db.session.commit()
            flash("Template created.", "success")
            return redirect(url_for("settings.email_templates_list", organization_id=org_id))
        except EmailServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
    return render_template(
        "settings/email_template_form.html",
        form=form,
        template=None,
        organization_id=org_id,
    )


@settings_bp.route("/email-templates", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
def email_templates_create():
    org_id = _settings_org_id(require_admin=True)
    return _create_email_template(org_id)


@settings_bp.route("/email-templates/new", methods=["GET"])
@login_required
@require_role("admin", "superadmin")
def email_templates_new_form():
    org_id = _settings_org_id(require_admin=True)
    return render_template(
        "settings/email_template_form.html",
        form=EmailTemplateForm(),
        template=None,
        organization_id=org_id,
    )


@settings_bp.route("/email-templates/<int:template_id>/edit", methods=["GET", "POST"])
@login_required
@require_role("admin", "superadmin")
def email_templates_edit(template_id):
    org_id = _settings_org_id(require_admin=True)
    try:
        template = TemplateService.get_template(template_id, org_id)
    except EmailServiceError:
        abort(404)
    if template.is_system and not current_user.is_superadmin():
        abort(403)

    form = EmailTemplateForm(obj=template)
    if form.validate_on_submit():
        try:
            TemplateService.update_template(
                template,
                current_user.id,
                name=form.name.data,
                subject_template=form.subject_template.data,
                body_html_template=form.body_html_template.data,
                body_text_template=form.body_text_template.data,
            )
            db.session.commit()
            flash("Template updated.", "success")
            return redirect(url_for("settings.email_templates_list", organization_id=org_id))
        except EmailServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
    return render_template(
        "settings/email_template_form.html",
        form=form,
        template=template,
        organization_id=org_id,
    )


@settings_bp.route("/email-templates/<int:template_id>", methods=["POST", "DELETE"])
@login_required
@require_role("admin", "superadmin")
def email_templates_delete(template_id):
    org_id = _settings_org_id(require_admin=True)
    try:
        template = TemplateService.get_template(template_id, org_id)
        TemplateService.delete_template(template, current_user.id)
        db.session.commit()
        flash("Template deleted.", "success")
    except EmailServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("settings.email_templates_list", organization_id=org_id))


@settings_bp.route("/email-templates/<int:template_id>/preview", methods=["GET"])
@login_required
@require_role("admin", "superadmin", "user")
def email_templates_preview(template_id):
    org_id = _settings_org_id()
    try:
        template = TemplateService.get_template(template_id, org_id)
    except EmailServiceError:
        abort(404)

    sample_lead = Lead(
        first_name="Matti",
        last_name="Meikäläinen",
        company="As Oy Esimerkkitalo",
        ai_summary="Taloyhtiössä on suunnitteilla putkiremontti vuodelle 2027.",
    )
    rendered = TemplateService.render_for_lead(template, sample_lead, sender_display_name())
    return render_template(
        "settings/email_template_preview.html",
        template=template,
        rendered=rendered,
        organization_id=org_id,
    )
