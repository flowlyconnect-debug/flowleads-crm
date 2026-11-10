"""Optional modules per organization (Organization.enabled_features).

The keys live in app/admin/constants.py FEATURE_CATALOG. A superadmin chooses
which ones a customer may use (invite / customer list); everything else in the
CRM is always on. Superadmins themselves are never restricted.
"""

from __future__ import annotations

from flask import flash, jsonify, redirect, request, url_for
from flask_login import current_user

from app.admin.constants import FEATURE_CATALOG
from app.extensions import db


def organization_has_feature(organization_id: int | None, key: str) -> bool:
    """True when the organization may use the module. Unknown organizations
    (None / deleted) are not restricted here; tenant checks handle those."""
    if organization_id is None:
        return True
    from app.users.models import Organization

    organization = db.session.get(Organization, organization_id)
    if organization is None:
        return True
    return organization.has_feature(key)


def current_user_has_feature(key: str) -> bool:
    if not current_user.is_authenticated:
        return False
    if current_user.is_superadmin():
        return True
    return organization_has_feature(current_user.organization_id, key)


def feature_guard(key: str):
    """before_request hook for a blueprint that belongs to one module.
    Anonymous requests pass (public token links, login redirects)."""

    def _guard():
        if not current_user.is_authenticated or current_user_has_feature(key):
            return None
        label = FEATURE_CATALOG.get(key, key)
        message = f"{label} ei ole käytössä organisaatiollanne. Ota yhteyttä FlowLeadsin ylläpitoon."
        wants_json = request.is_json or request.accept_mimetypes.best == "application/json"
        if wants_json or request.method not in ("GET", "HEAD"):
            return jsonify({"success": False, "error": message, "code": "feature_disabled"}), 403
        flash(message, "warning")
        return redirect(url_for("analytics.dashboard"))

    return _guard
