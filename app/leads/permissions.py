from flask import abort, request
from flask_login import current_user


def resolve_organization_id() -> int:
    if current_user.role == "superadmin":
        org_id = request.args.get("organization_id") or request.form.get("organization_id")
        if not org_id:
            abort(400, description="organization_id is required for superadmin.")
        try:
            return int(org_id)
        except (TypeError, ValueError):
            abort(400, description="Invalid organization_id.")
    if current_user.organization_id is None:
        abort(403)
    return current_user.organization_id


def can_manage_leads() -> bool:
    return current_user.role in ("superadmin", "admin", "user")


def can_assign_to_others() -> bool:
    return current_user.role in ("superadmin", "admin")


def can_archive_leads() -> bool:
    return current_user.role in ("superadmin", "admin")


def validate_assignment(assigned_to: int | None) -> int | None:
    if assigned_to is None or assigned_to == "":
        return None
    assigned_to = int(assigned_to)
    if not can_assign_to_others():
        if assigned_to != current_user.id:
            abort(403)
    return assigned_to
