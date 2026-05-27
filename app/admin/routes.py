from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin.services import get_accessible_organizations, get_dashboard_stats
from app.api.services import APIKeyServiceError, create_api_key, list_api_keys, revoke_api_key
from app.core.permissions import require_2fa, require_role
from app.extensions import db
from app.users.services import UserServiceError, create_organization

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/dashboard")
@login_required
@require_role("superadmin", "admin")
@require_2fa
def dashboard():
    stats = get_dashboard_stats()
    organizations = get_accessible_organizations()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        organizations=organizations,
    )


@admin_bp.route("/organizations", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def create_organization_route():
    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip().lower()
    try:
        org = create_organization(name, slug)
        db.session.commit()
        flash(f"Organisaatio luotu: {org.name}", "success")
    except UserServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/api-keys", methods=["GET"])
@login_required
@require_role("superadmin")
@require_2fa
def api_keys():
    keys = list_api_keys()
    organizations = get_accessible_organizations()
    new_key = request.args.get("new_key")
    return render_template(
        "admin/api_keys.html",
        keys=keys,
        organizations=organizations,
        new_key=new_key,
    )


@admin_bp.route("/api-keys", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def create_api_key_route():
    try:
        organization_id = int(request.form.get("organization_id") or 0)
    except (TypeError, ValueError):
        organization_id = 0

    name = request.form.get("name", "").strip()
    if not organization_id:
        flash("Organization is required.", "danger")
        return redirect(url_for("admin.api_keys"))

    from flask import current_app

    try:
        api_key, full_key = create_api_key(
            organization_id,
            name,
            created_by=current_user.id,
            test_mode=bool(current_app.config.get("TESTING")),
        )
        db.session.commit()
    except APIKeyServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
        return redirect(url_for("admin.api_keys"))

    return redirect(url_for("admin.api_keys", new_key=full_key))


@admin_bp.route("/predictions/run-batch", methods=["POST"])
@login_required
@require_role("superadmin", "admin")
@require_2fa
def run_predictions_batch():
    from app.leads.permissions import resolve_organization_id

    organization_id = resolve_organization_id()
    from app.analytics.prediction import PredictionService

    result = PredictionService.predict_batch(organization_id)
    flash(
        f"Ennusteet päivitetty: {result['processed']} onnistui, {result['failed']} epäonnistui.",
        "success" if result["failed"] == 0 else "warning",
    )
    return redirect(
        url_for(
            "analytics.forecast",
            organization_id=organization_id,
        )
        if current_user.is_superadmin()
        else url_for("analytics.forecast")
    )


@admin_bp.route("/api-keys/<int:key_id>", methods=["POST", "DELETE"])
@login_required
@require_role("superadmin")
@require_2fa
def revoke_api_key_route(key_id: int):
    try:
        revoke_api_key(key_id, revoked_by=current_user.id)
        db.session.commit()
        flash("API key revoked.", "success")
    except APIKeyServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("admin.api_keys"))
