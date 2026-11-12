"""GDPR routes registered on leads_bp and settings_bp."""

from flask import (
    abort,
    current_app,
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


def _org_kwargs(organization_id) -> dict:
    return {"organization_id": organization_id} if current_user.is_superadmin() else {}


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
        organization_id = resolve_organization_id()

        settings = get_privacy_settings(organization_id)
        form = PrivacySettingsForm()
        if request.method == "POST" and not form.validate():
            errors = [e for field_errors in form.errors.values() for e in field_errors]
            flash(errors[0] if errors else "Tarkista kentät.", "danger")
        elif form.validate_on_submit():
            update_privacy_settings(
                organization_id,
                {
                    "gdpr_default_legal_basis": form.gdpr_default_legal_basis.data,
                    "gdpr_retention_days": form.gdpr_retention_days.data or 730,
                    "gdpr_auto_anonymize_inactive": request.form.get("gdpr_auto_anonymize_inactive") == "on",
                    "privacy_policy_url": form.privacy_policy_url.data,
                },
            )
            db.session.commit()
            flash("Tietosuoja-asetukset tallennettu.", "success")
            return redirect(url_for("settings.privacy_settings", organization_id=organization_id))

        if request.method == "GET":
            form.gdpr_default_legal_basis.data = settings.gdpr_default_legal_basis or "legitimate_interest"
            form.gdpr_retention_days.data = settings.gdpr_retention_days or 730
            form.privacy_policy_url.data = settings.privacy_policy_url

        from datetime import datetime, timezone

        from app.gdpr.models import DataExportRequest

        exports = (
            DataExportRequest.query.filter(
                DataExportRequest.organization_id == organization_id,
                DataExportRequest.export_type == "organization",
                DataExportRequest.status == "completed",
                DataExportRequest.expires_at > datetime.now(timezone.utc),
            )
            .order_by(DataExportRequest.created_at.desc())
            .limit(3)
            .all()
        )
        return render_template(
            "settings/privacy.html",
            form=form,
            settings=settings,
            organization_id=organization_id,
            exports=exports,
        )

    @settings_bp.route("/export", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin")
    def gdpr_export_info():
        organization_id = resolve_organization_id()
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
        # The ZIP is built right here in the web process: the scheduler's disk
        # (where the background job used to write it) is not visible to the
        # web service on Render, so those downloads never worked.
        organization_id = resolve_organization_id()
        try:
            req = DataExportService.create_organization_export_request(
                organization_id, current_user.id
            )
            DataExportService._process_single_export(req)
            db.session.commit()
            flash("Tietojen vienti on valmis. Lataa ZIP alta (linkki on voimassa 48 tuntia).", "success")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Organization export failed for org=%s", organization_id)
            flash("Viennin luonti epäonnistui.", "danger")
        return redirect(url_for("settings.privacy_settings", **_org_kwargs(organization_id)))

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

        db.session.commit()  # a rebuilt ZIP's new path
        return send_file(
            req.file_path,
            as_attachment=True,
            download_name=req.file_path.split("/")[-1].split("\\")[-1],
        )
