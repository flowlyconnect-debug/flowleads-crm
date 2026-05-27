from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.api.services import APIKeyServiceError, create_api_key, list_api_keys, revoke_api_key
from app.core.permissions import require_role
from app.extensions import db

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


@settings_bp.route("/api-keys", methods=["GET"])
@login_required
@require_role("admin", "superadmin")
def api_keys():
    org_id = current_user.organization_id
    if current_user.is_superadmin():
        org_id_param = request.args.get("organization_id")
        if org_id_param:
            try:
                org_id = int(org_id_param)
            except (TypeError, ValueError):
                org_id = None
        else:
            org_id = None

    if org_id is None and not current_user.is_superadmin():
        from flask import abort

        abort(403)

    keys = list_api_keys(org_id) if org_id else []
    new_key = request.args.get("new_key")
    return render_template(
        "settings/api_keys.html",
        keys=keys,
        organization_id=org_id,
        new_key=new_key,
    )


@settings_bp.route("/api-keys", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
def create_api_key_route():
    from flask import current_app

    org_id = current_user.organization_id
    if current_user.is_superadmin():
        try:
            org_id = int(request.form.get("organization_id") or 0)
        except (TypeError, ValueError):
            org_id = 0
        if not org_id:
            flash("Organization is required.", "danger")
            return redirect(url_for("settings.api_keys"))

    name = request.form.get("name", "").strip()
    try:
        api_key, full_key = create_api_key(
            org_id,
            name,
            created_by=current_user.id,
            test_mode=bool(current_app.config.get("TESTING")),
        )
        db.session.commit()
    except APIKeyServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
        return redirect(url_for("settings.api_keys"))

    return redirect(url_for("settings.api_keys", new_key=full_key))


@settings_bp.route("/api-keys/<int:key_id>", methods=["POST", "DELETE"])
@login_required
@require_role("admin", "superadmin")
def revoke_api_key_route(key_id: int):
    org_scope = current_user.organization_id
    if current_user.is_superadmin():
        org_scope = None
    try:
        revoke_api_key(key_id, revoked_by=current_user.id, organization_id=org_scope)
        db.session.commit()
        flash("API key revoked.", "success")
    except APIKeyServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("settings.api_keys"))


import app.email.settings_routes  # noqa: F401, E402

from app.gdpr.routes import register_settings_gdpr_routes  # noqa: E402

register_settings_gdpr_routes(settings_bp)

from app.calendar.settings_routes import register_calendar_settings_routes  # noqa: E402

register_calendar_settings_routes(settings_bp)

from app.proposals.routes import register_proposal_settings_routes  # noqa: E402

register_proposal_settings_routes(settings_bp)

from app.webhooks.routes import register_settings_webhook_routes  # noqa: E402

register_settings_webhook_routes(settings_bp)
