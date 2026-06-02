import secrets
import time

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.api.services import APIKeyServiceError, create_api_key, list_api_keys, revoke_api_key
from app.core.permissions import require_role
from app.extensions import db

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")
_PENDING_API_KEYS_SESSION = "pending_api_keys"
_PENDING_API_KEY_TTL_SECONDS = 15 * 60


def _store_pending_api_key(org_id: int, full_key: str) -> None:
    pending = session.get(_PENDING_API_KEYS_SESSION, {})
    token = secrets.token_urlsafe(24)
    pending[token] = {
        "org_id": int(org_id),
        "full_key": full_key,
        "expires_at": int(time.time()) + _PENDING_API_KEY_TTL_SECONDS,
    }
    session[_PENDING_API_KEYS_SESSION] = pending
    session.modified = True


def _pending_api_key_exists(org_id: int) -> bool:
    now = int(time.time())
    pending = session.get(_PENDING_API_KEYS_SESSION, {})
    changed = False
    exists = False
    for token, entry in list(pending.items()):
        if int(entry.get("expires_at", 0)) < now:
            pending.pop(token, None)
            changed = True
            continue
        if int(entry.get("org_id", -1)) == int(org_id):
            exists = True
    if changed:
        session[_PENDING_API_KEYS_SESSION] = pending
        session.modified = True
    return exists


def _consume_pending_api_key(org_id: int) -> str | None:
    now = int(time.time())
    pending = session.get(_PENDING_API_KEYS_SESSION, {})
    for token, entry in list(pending.items()):
        if int(entry.get("expires_at", 0)) < now:
            pending.pop(token, None)
            continue
        if int(entry.get("org_id", -1)) == int(org_id):
            full_key = str(entry.get("full_key", "")).strip()
            pending.pop(token, None)
            session[_PENDING_API_KEYS_SESSION] = pending
            session.modified = True
            return full_key or None
    session[_PENDING_API_KEYS_SESSION] = pending
    session.modified = True
    return None


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
        abort(403)

    keys = list_api_keys(org_id) if org_id else []
    return render_template(
        "settings/api_keys.html",
        keys=keys,
        organization_id=org_id,
        has_pending_key=_pending_api_key_exists(org_id) if org_id else False,
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

    _store_pending_api_key(org_id, full_key)
    redirect_kwargs = {}
    if current_user.is_superadmin():
        redirect_kwargs["organization_id"] = org_id
    return redirect(url_for("settings.api_keys", **redirect_kwargs))


@settings_bp.route("/api-keys/reveal-latest", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
def reveal_latest_api_key():
    org_id = current_user.organization_id
    if current_user.is_superadmin():
        try:
            org_id = int(request.form.get("organization_id") or request.args.get("organization_id") or 0)
        except (TypeError, ValueError):
            org_id = 0
    if not org_id:
        return jsonify({"success": False, "error": "Organization is required."}), 400
    full_key = _consume_pending_api_key(org_id)
    if not full_key:
        return jsonify({"success": False, "error": "No pending API key available."}), 404
    return jsonify({"success": True, "data": {"api_key": full_key}})


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

from app.streams.routes import register_stream_settings_routes  # noqa: E402

register_stream_settings_routes(settings_bp)

from app.search.routes import register_search_profile_settings_routes  # noqa: E402

register_search_profile_settings_routes(settings_bp)
