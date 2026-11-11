"""Organization / tenant resolution helpers for UI and API routes."""

from __future__ import annotations

from flask import abort, flash, redirect, request, session, url_for
from flask_login import current_user

from app.extensions import db
from app.users.models import Organization


_SESSION_ORG_KEY = "superadmin_org_id"


def parse_organization_id_param() -> int | None:
    """organization_id from query/form; None if absent.

    A superadmin has no organization of their own and picks one with
    ?organization_id=. The choice is remembered in the session, so a link or
    redirect that drops the parameter stays in the same organization instead
    of ending on a 400 page."""
    raw = request.args.get("organization_id") or request.form.get("organization_id")
    is_superadmin = current_user.is_authenticated and current_user.role == "superadmin"
    if raw in (None, ""):
        if is_superadmin:
            remembered = session.get(_SESSION_ORG_KEY)
            if remembered and organization_exists(remembered):
                return remembered
        return None
    try:
        org_id = int(raw)
    except (TypeError, ValueError):
        return None
    if is_superadmin and organization_exists(org_id):
        session[_SESSION_ORG_KEY] = org_id
    return org_id


def forget_remembered_organization() -> None:
    session.pop(_SESSION_ORG_KEY, None)


def organization_exists(organization_id: int) -> bool:
    return db.session.get(Organization, organization_id) is not None


def resolve_organization_id() -> int:
    """Resolve tenant organization_id for the current user."""
    if current_user.role == "superadmin":
        org_id = parse_organization_id_param()
        if org_id is None:
            wants_json = request.path.startswith("/api") or request.is_json
            if request.method == "GET" and not wants_json:
                # a page that needs an organization: send the superadmin to pick one
                flash("Valitse ensin organisaatio, jonka tietoja haluat katsoa.", "info")
                abort(redirect(url_for("analytics.dashboard")))
            abort(400, description="organization_id is required for superadmin.")
        if not organization_exists(org_id):
            abort(404, description="Organization not found.")
        return org_id

    if current_user.organization_id is None:
        abort(403)
    return current_user.organization_id


def resolve_organization_id_with_fallback() -> int | None:
    """Resolve organization_id; superadmin may fall back to their own org."""
    if current_user.role == "superadmin":
        org_id = parse_organization_id_param()
        if org_id is not None and not organization_exists(org_id):
            org_id = None
        if org_id is None and current_user.organization_id:
            org_id = current_user.organization_id
        if org_id is None or not organization_exists(org_id):
            return None
        return org_id

    if current_user.organization_id is None:
        return None
    return current_user.organization_id


def optional_organization_id() -> int | None:
    """Superadmin may omit organization_id (org picker flows); others use their org."""
    if current_user.role == "superadmin":
        org_id = parse_organization_id_param()
        if org_id is None:
            return None
        if not organization_exists(org_id):
            abort(404, description="Organization not found.")
        return org_id

    if current_user.organization_id is None:
        abort(403)
    return current_user.organization_id


def org_query_suffix(organization_id: int) -> dict:
    if current_user.is_superadmin():
        return {"organization_id": organization_id}
    return {}


def url_query_suffix() -> dict:
    """Organization query params for templates from the current request."""
    if not current_user.is_authenticated or not current_user.is_superadmin():
        return {}
    org_id = parse_organization_id_param()
    if org_id is None:
        return {}
    return {"organization_id": org_id}
