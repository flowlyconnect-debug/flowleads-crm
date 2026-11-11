import secrets
import time

from flask import Blueprint, abort, jsonify, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.admin.constants import FEATURE_CATALOG
from app.admin.invite_services import InviteServiceError, activate_source, create_invite, renew_invite
from app.admin.onboarding_services import CustomerOnboardingError, create_customer
from app.admin.services import get_accessible_organizations, get_dashboard_stats, list_customers_summary
from app.api.services import APIKeyServiceError, create_api_key, list_api_keys, revoke_api_key
from app.core.permissions import require_2fa, require_role
from app.extensions import db
from app.search.constants import ALL_FINLAND_REGION, FINNISH_REGIONS, REMONTTITYYPIT
from app.search.models import SearchProfile
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
    return render_template("admin/customers.html", customers=customers, feature_catalog=FEATURE_CATALOG)


@admin_bp.route("/customers/<int:organization_id>/active", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def set_customer_active_route(organization_id: int):
    """Switch a customer organization off or back on. Off: its users cannot
    sign in, open sessions end, API keys stop working. Data is kept."""
    from app.core.audit import log_audit
    from app.users.models import Organization

    organization = db.session.get(Organization, organization_id)
    if organization is None:
        abort(404)
    organization.is_active = request.form.get("active") == "1"
    log_audit(
        "organization_activated" if organization.is_active else "organization_deactivated",
        user_id=current_user.id,
        organization_id=organization.id,
        target_type="organization",
        target_id=organization.id,
    )
    db.session.commit()
    flash(
        f"{organization.name}: " + ("otettu käyttöön." if organization.is_active else "poistettu käytöstä."),
        "success",
    )
    return redirect(url_for("admin.customers_list"))


@admin_bp.route("/users/<int:user_id>/reset-2fa", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def reset_user_two_factor_route(user_id: int):
    """A user lost their phone and backup codes: clear the second factor so
    they set it up again at the next sign-in (2FA stays mandatory)."""
    from app.auth.models import BackupCode
    from app.core.audit import log_audit
    from app.users.models import User

    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == current_user.id:
        flash("Et voi nollata omaa kaksivaiheista tunnistautumistasi.", "danger")
    else:
        user.totp_enabled = False
        user.totp_secret = None
        BackupCode.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        log_audit(
            "two_factor_reset",
            user_id=current_user.id,
            organization_id=user.organization_id,
            target_type="user",
            target_id=user.id,
            metadata={"email": user.email},
        )
        db.session.commit()
        flash(
            f"Kaksivaiheinen tunnistautuminen nollattu: {user.email}. "
            "Käyttäjä ottaa sen uudelleen käyttöön seuraavalla kirjautumisella.",
            "success",
        )
    if user.organization_id:
        return redirect(url_for("settings.team_members", organization_id=user.organization_id))
    return redirect(url_for("admin.customers_list"))


AUDIT_ACTION_LABELS_FI = {
    "login_success": "Kirjautuminen",
    "login_failed": "Epäonnistunut kirjautuminen",
    "logout": "Uloskirjautuminen",
    "customer_invited": "Asiakas kutsuttu",
    "customer_created": "Asiakas luotu",
    "targets_transferred": "Kohteita siirretty liideiksi",
    "leads_imported": "Liidejä tuotu tiedostosta",
    "leads_purged": "Liidit poistettu",
    "lead_archived": "Liidi arkistoitu",
    "lead_restored": "Liidi palautettu",
    "lead_anonymized": "Liidi anonymisoitu",
    "organization_activated": "Organisaatio otettu käyttöön",
    "organization_deactivated": "Organisaatio poistettu käytöstä",
    "two_factor_reset": "2FA nollattu",
    "backup_created": "Varmuuskopio luotu",
    "backup_failed": "Varmuuskopiointi epäonnistui",
    "email_template_created": "Sähköpostimalli luotu",
    "email_template_updated": "Sähköpostimalli muokattu",
    "api_key_created": "API-avain luotu",
    "api_key_revoked": "API-avain poistettu käytöstä",
}


@admin_bp.route("/audit-log", methods=["GET"])
@login_required
@require_role("superadmin")
@require_2fa
def audit_log():
    """Who did what: the audit trail the app has always written, browsable."""
    from app.users.models import AuditLog, Organization

    organization_id = request.args.get("org", type=int)
    action = (request.args.get("action") or "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)

    query = AuditLog.query
    if organization_id:
        query = query.filter(AuditLog.organization_id == organization_id)
    if action:
        query = query.filter(AuditLog.action == action)
    pagination = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).paginate(
        page=page, per_page=50, error_out=False
    )
    organizations = Organization.query.order_by(Organization.name.asc()).all()
    actions = [row[0] for row in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]
    return render_template(
        "admin/audit_log.html",
        pagination=pagination,
        entries=pagination.items,
        organizations=organizations,
        organization_names={o.id: o.name for o in organizations},
        actions=actions,
        action_labels=AUDIT_ACTION_LABELS_FI,
        selected_org=organization_id,
        selected_action=action,
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

    if request.form.get("region_mode") == "all_finland":
        regions = [ALL_FINLAND_REGION]
    else:
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
            "temporary_password": result.temporary_password,
            "is_active": result.organization.is_active,
            "login_url": url_for("auth.login", _external=True),
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


_PENDING_INVITE_RESULT_SESSION = "pending_invite_result"


def _store_invite_result(result: dict) -> None:
    session[_PENDING_INVITE_RESULT_SESSION] = {
        **result,
        "expires_at": int(time.time()) + _PENDING_ADMIN_API_KEY_TTL_SECONDS,
    }
    session.modified = True


def _consume_invite_result() -> dict | None:
    entry = session.get(_PENDING_INVITE_RESULT_SESSION)
    if not entry:
        return None
    if int(entry.get("expires_at", 0)) < int(time.time()):
        session.pop(_PENDING_INVITE_RESULT_SESSION, None)
        session.modified = True
        return None
    session.pop(_PENDING_INVITE_RESULT_SESSION, None)
    session.modified = True
    return entry


def _send_invite_and_store_result(*, organization, invite, token) -> None:
    from app.admin.models import INVITE_DEFAULT_TTL_DAYS
    from app.email.services import EmailService

    invite_url = url_for("invite.accept_invite", token=token, _external=True)
    EmailService.send_customer_invite_email(
        to_email=invite.email,
        invite_url=invite_url,
        organization_name=organization.name,
        ttl_days=INVITE_DEFAULT_TTL_DAYS,
    )
    _store_invite_result(
        {
            "organization_name": organization.name,
            "admin_email": invite.email,
            "invite_url": invite_url,
            "expires_at_label": invite.expires_at.strftime("%d.%m.%Y %H:%M UTC"),
        }
    )


@admin_bp.route("/customers/invite", methods=["GET", "POST"])
@login_required
@require_role("superadmin")
@require_2fa
def create_invite_route():
    if request.method == "GET":
        return render_template(
            "admin/customer_invite.html",
            remonttityypit=REMONTTITYYPIT,
            feature_catalog=FEATURE_CATALOG,
            form_data={},
        )

    allowed_features = request.form.getlist("allowed_features")
    form_data = {
        "organization_name": request.form.get("organization_name", "").strip(),
        "admin_email": request.form.get("admin_email", "").strip(),
        "search_profile_name": request.form.get("search_profile_name", "").strip(),
        "remonttityyppi": request.form.get("remonttityyppi", "").strip(),
        "source": (request.form.get("source") or "oikotie").strip(),
        "allowed_features": allowed_features,
    }

    try:
        result = create_invite(
            actor=current_user,
            organization_name=form_data["organization_name"],
            admin_email=form_data["admin_email"],
            search_profile_name=form_data["search_profile_name"],
            remonttityyppi=form_data["remonttityyppi"],
            source=form_data["source"],
            allowed_features=allowed_features,
        )
        db.session.commit()
    except InviteServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
        return render_template(
            "admin/customer_invite.html",
            remonttityypit=REMONTTITYYPIT,
            feature_catalog=FEATURE_CATALOG,
            form_data=form_data,
        )

    _send_invite_and_store_result(organization=result.organization, invite=result.invite, token=result.token)
    db.session.commit()
    return redirect(url_for("admin.create_invite_result"))


@admin_bp.route("/customers/invite/result")
@login_required
@require_role("superadmin")
@require_2fa
def create_invite_result():
    result = _consume_invite_result()
    if not result:
        flash("Kutsun tulosta ei ole enää saatavilla.", "warning")
        return redirect(url_for("admin.create_invite_route"))
    return render_template("admin/customer_invite_result.html", result=result)


@admin_bp.route("/customers/invite/<int:invite_id>/renew", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def renew_invite_route(invite_id: int):
    from app.admin.models import CustomerInvite

    invite = db.session.get(CustomerInvite, invite_id)
    if invite is None:
        abort(404)

    result = renew_invite(invite, actor=current_user)
    db.session.commit()
    _send_invite_and_store_result(organization=result.organization, invite=result.invite, token=result.token)
    db.session.commit()
    return redirect(url_for("admin.create_invite_result"))


@admin_bp.route("/customers/profiles/<int:profile_id>/activate-source", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def activate_source_route(profile_id: int):
    profile = db.session.get(SearchProfile, profile_id)
    if profile is None:
        abort(404)

    try:
        activate_source(profile, actor=current_user)
        db.session.commit()
        flash(
            f"Hakuprofiili aktivoitu: {profile.name}. Osumat katalogin viime viikoilta "
            "ilmestyvät asiakkaan Kohteet-sivulle noin 10 minuutin kuluessa.",
            "success",
        )
    except InviteServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("admin.customers_list"))


@admin_bp.route("/customers/<int:organization_id>/features", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def update_customer_features_route(organization_id: int):
    from app.core.audit import log_audit
    from app.users.models import Organization

    organization = db.session.get(Organization, organization_id)
    if organization is None:
        abort(404)
    features = [key for key in FEATURE_CATALOG if key in request.form.getlist("features")]
    organization.enabled_features = features
    log_audit(
        "organization_features_updated",
        user_id=current_user.id,
        organization_id=organization.id,
        target_type="organization",
        target_id=organization.id,
        metadata={"enabled_features": features},
    )
    db.session.commit()
    flash(f"Toiminnot päivitetty: {organization.name}.", "success")
    return redirect(url_for("admin.customers_list"))


@admin_bp.route("/catalogue")
@login_required
@require_role("superadmin")
@require_2fa
def catalogue_status():
    from app.listings.services import catalogue_health

    return render_template("admin/catalogue.html", health=catalogue_health(), remonttityypit=REMONTTITYYPIT)


@admin_bp.route("/catalogue/import", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def catalogue_import():
    """CSV import of listings from a source other than the Oikotie crawler."""
    from app.core.audit import log_audit
    from app.listings.import_services import import_listings, parse_listing_csv
    from app.listings.services import ListingServiceError

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("Valitse CSV-tiedosto.", "danger")
        return redirect(url_for("admin.catalogue_status"))
    try:
        rows = parse_listing_csv(upload.read(5 * 1024 * 1024 + 1)[: 5 * 1024 * 1024])
        result = import_listings(rows)
    except ListingServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
        return redirect(url_for("admin.catalogue_status"))

    log_audit(
        action="catalogue_import",
        user_id=current_user.id,
        organization_id=None,
        target_type="listing",
        target_id=None,
        metadata={k: v for k, v in result.items() if k != "errors"} | {"errors": len(result["errors"])},
    )
    db.session.commit()
    flash(
        f"Tuotu {result['created']} uutta ja päivitetty {result['updated']} kohdetta. "
        f"Asiakkaiden Kohteet-sivuille tuli {result['matches']} osumaa, "
        f"AI-analyysiin jonossa {result['queued']}.",
        "success",
    )
    if result["errors"]:
        shown = "; ".join(f"rivi {e['row'] + 1}: {e['error']}" for e in result["errors"][:8])
        more = len(result["errors"]) - 8
        flash(
            f"{len(result['errors'])} riviä ohitettiin. {shown}" + (f" (+{more} muuta)" if more > 0 else ""),
            "warning",
        )
    return redirect(url_for("admin.catalogue_status"))


@admin_bp.route("/catalogue/import-template.csv")
@login_required
@require_role("superadmin")
@require_2fa
def catalogue_import_template():
    from flask import Response

    from app.listings.import_services import csv_template

    # BOM so Excel opens the file as UTF-8
    return Response(
        "﻿" + csv_template(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=flowleads-kohteet-malli.csv"},
    )


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


@admin_bp.route("/dev/reset-running-test-jobs", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
@require_2fa
def reset_running_test_jobs_route():
    from flask import current_app

    if not current_app.config.get("ENABLE_TEST_JOBS"):
        abort(403)

    from app.search.job_scheduler import reset_running_test_jobs

    result = reset_running_test_jobs()
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
