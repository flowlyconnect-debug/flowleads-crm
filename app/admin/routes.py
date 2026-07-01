import secrets
import time

from flask import Blueprint, abort, jsonify, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.admin.onboarding_services import CustomerOnboardingError, create_customer
from app.admin.services import get_accessible_organizations, get_dashboard_stats, list_customers_summary
from app.api.services import APIKeyServiceError, create_api_key, list_api_keys, revoke_api_key
from app.core.permissions import require_2fa, require_role
from app.extensions import db
from app.search.constants import FINNISH_REGIONS, REMONTTITYYPIT
from app.users.services import UserServiceError, create_organization

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
_PENDING_ADMIN_API_KEYS_SESSION = "pending_admin_api_keys"
_PENDING_ONBOARDING_RESULT_SESSION = "pending_onboarding_result"
_PENDING_ADMIN_API_KEY_TTL_SECONDS = 15 * 60


def _store_pending_admin_api_key(organization_id: int, full_key: str) -> None:
    pending = session.get(_PENDING_ADMIN_API_KEYS_SESSION, {})
    token = secrets.token_urlsafe(24)
    pending[token] = {
        "organization_id": int(organization_id),
        "full_key": full_key,
        "expires_at": int(time.time()) + _PENDING_ADMIN_API_KEY_TTL_SECONDS,
    }
    session[_PENDING_ADMIN_API_KEYS_SESSION] = pending
    session.modified = True


def _pending_admin_api_key_exists() -> bool:
    now = int(time.time())
    pending = session.get(_PENDING_ADMIN_API_KEYS_SESSION, {})
    changed = False
    exists = False
    for token, entry in list(pending.items()):
        if int(entry.get("expires_at", 0)) < now:
            pending.pop(token, None)
            changed = True
            continue
        exists = True
    if changed:
        session[_PENDING_ADMIN_API_KEYS_SESSION] = pending
        session.modified = True
    return exists


def _consume_pending_admin_api_key() -> str | None:
    now = int(time.time())
    pending = session.get(_PENDING_ADMIN_API_KEYS_SESSION, {})
    for token, entry in list(pending.items()):
        if int(entry.get("expires_at", 0)) < now:
            pending.pop(token, None)
            continue
        full_key = str(entry.get("full_key", "")).strip()
        pending.pop(token, None)
        session[_PENDING_ADMIN_API_KEYS_SESSION] = pending
        session.modified = True
        return full_key or None
    session[_PENDING_ADMIN_API_KEYS_SESSION] = pending
    session.modified = True
    return None


def _store_onboarding_result(result: dict) -> None:
    session[_PENDING_ONBOARDING_RESULT_SESSION] = {
        **result,
        "expires_at": int(time.time()) + _PENDING_ADMIN_API_KEY_TTL_SECONDS,
    }
    session.modified = True


def _consume_onboarding_result() -> dict | None:
    entry = session.get(_PENDING_ONBOARDING_RESULT_SESSION)
    if not entry:
        return None
    if int(entry.get("expires_at", 0)) < int(time.time()):
        session.pop(_PENDING_ONBOARDING_RESULT_SESSION, None)
        session.modified = True
        return None
    session.pop(_PENDING_ONBOARDING_RESULT_SESSION, None)
    session.modified = True
    return entry


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


@admin_bp.route("/customers")
@login_required
@require_role("superadmin")
@require_2fa
def customers_list():
    customers = list_customers_summary()
    return render_template("admin/customers.html", customers=customers)


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


@admin_bp.route("/customers/new", methods=["GET", "POST"])
@login_required
@require_role("superadmin")
@require_2fa
def create_customer_route():
    if request.method == "GET":
        return render_template(
            "admin/customer_onboarding.html",
            remonttityypit=REMONTTITYYPIT,
            finnish_regions=FINNISH_REGIONS,
            form_data={},
        )

    regions = [region for region in request.form.getlist("regions") if region in FINNISH_REGIONS]
    form_data = {
        "organization_name": request.form.get("organization_name", "").strip(),
        "admin_name": request.form.get("admin_name", "").strip(),
        "admin_email": request.form.get("admin_email", "").strip(),
        "temporary_password": request.form.get("temporary_password", "").strip(),
        "search_profile_name": request.form.get("search_profile_name", "").strip(),
        "regions": regions,
        "remonttityyppi": request.form.get("remonttityyppi", "").strip(),
        "source": (request.form.get("source") or "oikotie").strip(),
        "is_active": request.form.get("is_active") == "y",
    }

    try:
        result = create_customer(
            actor=current_user,
            organization_name=form_data["organization_name"],
            admin_email=form_data["admin_email"],
            admin_password=form_data["temporary_password"] or None,
            admin_name=form_data["admin_name"] or None,
            search_profile_name=form_data["search_profile_name"],
            regions=regions,
            remonttityyppi=form_data["remonttityyppi"],
            source=form_data["source"],
            is_active=form_data["is_active"],
        )
        db.session.commit()
    except CustomerOnboardingError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
        return render_template(
            "admin/customer_onboarding.html",
            remonttityypit=REMONTTITYYPIT,
            finnish_regions=FINNISH_REGIONS,
            form_data=form_data,
        )

    _store_onboarding_result(
        {
            "organization_name": result.organization.name,
            "organization_slug": result.organization.slug,
            "admin_email": result.admin_user.email,
            "admin_name": form_data["admin_name"] or None,
            "search_profile_name": result.search_profile_name,
            "api_key_full": result.api_key_full,
            "temporary_password": result.temporary_password,
            "is_active": result.organization.is_active,
            "initial_job_created": result.initial_job_created,
        }
    )
    return redirect(url_for("admin.create_customer_result"))


@admin_bp.route("/customers/result")
@login_required
@require_role("superadmin")
@require_2fa
def create_customer_result():
    result = _consume_onboarding_result()
    if not result:
        flash("Asiakkaan luontitulosta ei ole enää saatavilla.", "warning")
        return redirect(url_for("admin.create_customer_route"))
    return render_template("admin/customer_onboarding_result.html", result=result)


@admin_bp.route("/api-keys", methods=["GET"])
@login_required
@require_role("superadmin")
@require_2fa
def api_keys():
    keys = list_api_keys()
    organizations = get_accessible_organizations()
    return render_template(
        "admin/api_keys.html",
        keys=keys,
        organizations=organizations,
        has_pending_key=_pending_admin_api_key_exists(),
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

    _store_pending_admin_api_key(organization_id, full_key)
    return redirect(url_for("admin.api_keys"))


@admin_bp.route("/api-keys/reveal-latest", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def reveal_latest_api_key():
    full_key = _consume_pending_admin_api_key()
    if not full_key:
        return jsonify({"success": False, "error": "No pending API key available."}), 404
    return jsonify({"success": True, "data": {"api_key": full_key}})


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


@admin_bp.route("/dev/create-test-jobs", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
@require_2fa
def create_test_jobs_route():
    from flask import current_app

    if not current_app.config.get("ENABLE_TEST_JOBS"):
        abort(403)

    from app.search.job_scheduler import create_missing_test_jobs

    result = create_missing_test_jobs()
    return jsonify(result)


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
