"""GDPR routes registered on leads_bp and settings_bp."""

from flask import (
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from app.core.audit import log_audit
from app.core.permissions import require_role
from app.extensions import db
from app.gdpr.exports import DataExportService, DataExportServiceError
from app.gdpr.forms import AnonymizeLeadForm, PrivacySettingsForm
from app.gdpr.services import GDPRService, GDPRServiceError
from app.gdpr.settings import get_privacy_settings, privacy_settings_to_dict, update_privacy_settings
from app.leads.permissions import resolve_organization_id
from app.leads.services import get_lead_for_org, LeadServiceError


def register_lead_gdpr_routes(leads_bp):
    @leads_bp.route("/<int:lead_id>/gdpr/anonymize", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    def gdpr_anonymize_lead(lead_id: int):
        organization_id = resolve_organization_id()
        form = AnonymizeLeadForm()
        if not form.validate_on_submit():
            flash("Virheellinen pyyntö.", "danger")
            return redirect(
                url_for(
                    "leads.detail",
                    lead_id=lead_id,
                    organization_id=organization_id if current_user.is_superadmin() else None,
                )
            )
        try:
            GDPRService.verify_password(current_user, form.password.data)
            GDPRService.anonymize_lead(
                lead_id,
                current_user.id,
                organization_id,
                reason=form.reason.data,
            )
            db.session.commit()
            flash("Liidin tiedot anonymisoitu.", "success")
        except GDPRServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
        return redirect(
            url_for(
                "leads.detail",
                lead_id=lead_id,
                organization_id=organization_id if current_user.is_superadmin() else None,
            )
        )

    @leads_bp.route("/<int:lead_id>/export", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin", "user")
    def gdpr_export_lead(lead_id: int):
        organization_id = resolve_organization_id()
        try:
            get_lead_for_org(lead_id, organization_id)
        except LeadServiceError:
            abort(404)
        try:
            payload = DataExportService.export_lead(lead_id, organization_id)
        except DataExportServiceError as exc:
            abort(404 if exc.code == "not_found" else 400)

        log_audit(
            "gdpr_data_exported",
            user_id=current_user.id,
            organization_id=organization_id,
            target_type="lead",
            target_id=lead_id,
        )
        db.session.commit()
        return jsonify(payload)


def register_settings_gdpr_routes(settings_bp):
    @settings_bp.route("/privacy", methods=["GET", "POST"])
    @login_required
    @require_role("admin", "superadmin")
    def privacy_settings():
        organization_id = current_user.organization_id
        if current_user.is_superadmin():
            org_param = request.args.get("organization_id") or request.form.get("organization_id")
            if org_param:
                try:
                    organization_id = int(org_param)
                except (TypeError, ValueError):
                    pass

        settings = get_privacy_settings(organization_id)
        form = PrivacySettingsForm()
        if form.validate_on_submit():
            update_privacy_settings(
                organization_id,
                {
                    "gdpr_default_legal_basis": form.gdpr_default_legal_basis.data,
                    "gdpr_retention_days": form.gdpr_retention_days.data or 730,
                    "gdpr_auto_anonymize_inactive": request.form.get("gdpr_auto_anonymize_inactive") == "on",
                    "privacy_policy_url": form.privacy_policy_url.data,
                    "data_controller_name": form.data_controller_name.data,
                    "data_controller_email": form.data_controller_email.data,
                },
            )
            db.session.commit()
            flash("Tietosuoja-asetukset tallennettu.", "success")
            return redirect(url_for("settings.privacy_settings", organization_id=organization_id))

        if request.method == "GET":
            form.gdpr_default_legal_basis.data = settings.gdpr_default_legal_basis
            form.gdpr_retention_days.data = str(settings.gdpr_retention_days or 730)
            form.privacy_policy_url.data = settings.privacy_policy_url
            form.data_controller_name.data = settings.data_controller_name
            form.data_controller_email.data = settings.data_controller_email

        return render_template(
            "settings/privacy.html",
            form=form,
            settings=settings,
            organization_id=organization_id,
        )

    @settings_bp.route("/export", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin")
    def gdpr_export_info():
        organization_id = current_user.organization_id
        settings = get_privacy_settings(organization_id)
        return jsonify(
            {
                "export_endpoint": url_for("settings.gdpr_export_request"),
                "privacy": privacy_settings_to_dict(settings),
            }
        )

    @settings_bp.route("/export/request", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    def gdpr_export_request():
        organization_id = current_user.organization_id
        try:
            req = DataExportService.create_organization_export_request(
                organization_id, current_user.id
            )
            db.session.commit()
            flash(
                "Organisaation tietojen vienti käynnistetty. Saat sähköpostiin latauslinkin kun valmis.",
                "success",
            )
        except Exception:
            db.session.rollback()
            flash("Viennin luonti epäonnistui.", "danger")
        return redirect(url_for("settings.privacy_settings"))

    @settings_bp.route("/export/download/<token>", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin")
    def gdpr_export_download(token: str):
        try:
            req = DataExportService.get_export_by_token(token)
        except DataExportServiceError as exc:
            flash(exc.message, "danger")
            return redirect(url_for("settings.privacy_settings"))

        if req.organization_id != current_user.organization_id and not current_user.is_superadmin():
            abort(403)

        return send_file(
            req.file_path,
            as_attachment=True,
            download_name=req.file_path.split("/")[-1].split("\\")[-1],
        )
