"""Organization / tenant resolution helpers for UI and API routes."""

from __future__ import annotations

from flask import abort, request
from flask_login import current_user

from app.extensions import db
from app.users.models import Organization


def parse_organization_id_param() -> int | None:
    """Read organization_id from query/form; None if absent."""
    raw = request.args.get("organization_id") or request.form.get("organization_id")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def organization_exists(organization_id: int) -> bool:
    return db.session.get(Organization, organization_id) is not None


def resolve_organization_id() -> int:
    """Resolve tenant organization_id for the current user."""
    if current_user.role == "superadmin":
        org_id = parse_organization_id_param()
        if org_id is None:
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
