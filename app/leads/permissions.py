from flask import abort, request
from flask_login import current_user

from app.core.tenant import resolve_organization_id


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
