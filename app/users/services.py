from datetime import datetime, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.core.security import hash_password, normalize_email, validate_email, validate_password
from app.extensions import db
from app.users.models import ROLES, Organization, User


class UserServiceError(Exception):
    def __init__(self, message: str, code: str = "user_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def get_user_by_email(email: str | None) -> User | None:
    normalized = normalize_email(email)
    if not normalized:
        return None
    return User.query.filter_by(email=normalized).first()


def get_user_by_id(user_id: int) -> User | None:
    if user_id is None:
        return None
    return db.session.get(User, user_id)


def ensure_same_organization(actor: User, target: User) -> None:
    if actor.role == "superadmin":
        return
    if actor.organization_id is None or target.organization_id is None:
        raise UserServiceError("Cross-tenant access denied.", "cross_tenant")
    if actor.organization_id != target.organization_id:
        raise UserServiceError("Cross-tenant access denied.", "cross_tenant")


def create_user(
    email: str,
    password: str,
    role: str = "user",
    organization_id: int | None = None,
    *,
    actor: User | None = None,
    is_active: bool = True,
) -> User:
    normalized = normalize_email(email)
    if not normalized or not validate_email(normalized):
        raise UserServiceError("A valid email address is required.", "invalid_email")

    if role not in ROLES:
        raise UserServiceError("Invalid role.", "invalid_role")

    ok, msg = validate_password(password)
    if not ok:
        raise UserServiceError(msg or "Invalid password.", "invalid_password")

    if role == "superadmin" and organization_id is not None:
        raise UserServiceError("Superadmin cannot belong to an organization.", "invalid_organization")

    if role != "superadmin" and organization_id is None:
        raise UserServiceError("Organization is required for this role.", "invalid_organization")

    if actor and actor.role != "superadmin":
        if organization_id != actor.organization_id:
            raise UserServiceError("Cross-tenant access denied.", "cross_tenant")

    if get_user_by_email(normalized):
        raise UserServiceError("Email is already registered.", "duplicate_email")

    user = User(
        email=normalized,
        password_hash=hash_password(password),
        role=role,
        organization_id=organization_id if role != "superadmin" else None,
        is_active=is_active,
    )
    db.session.add(user)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise UserServiceError("Email is already registered.", "duplicate_email") from None

    log_audit(
        "user_created",
        user_id=actor.id if actor else None,
        organization_id=user.organization_id,
        target_type="user",
        target_id=user.id,
        metadata={"email": normalized, "role": role},
    )
    return user


def delete_user(user: User, *, actor: User) -> None:
    if actor.id == user.id:
        raise UserServiceError("You cannot delete your own account.", "self_delete")
    ensure_same_organization(actor, user)
    if actor.role != "superadmin" and user.role in ("superadmin", "admin"):
        raise UserServiceError("Insufficient permissions.", "forbidden")

    log_audit(
        "user_deleted",
        user_id=actor.id,
        organization_id=user.organization_id,
        target_type="user",
        target_id=user.id,
        metadata={"email": user.email},
    )
    db.session.delete(user)


def change_user_role(user: User, new_role: str, *, actor: User) -> User:
    if new_role not in ROLES:
        raise UserServiceError("Invalid role.", "invalid_role")
    ensure_same_organization(actor, user)
    if actor.role != "superadmin":
        raise UserServiceError("Insufficient permissions.", "forbidden")

    old_role = user.role
    user.role = new_role
    log_audit(
        "role_changed",
        user_id=actor.id,
        organization_id=user.organization_id,
        target_type="user",
        target_id=user.id,
        metadata={"old_role": old_role, "new_role": new_role},
    )
    return user


def create_organization(name: str, slug: str) -> Organization:
    from app.core.security import validate_slug

    if not name or not name.strip() or len(name.strip()) > 255:
        raise UserServiceError("Organization name is required.", "invalid_name")
    slug = (slug or "").strip().lower()
    if not validate_slug(slug):
        raise UserServiceError("Invalid organization slug.", "invalid_slug")

    org = Organization(name=name.strip(), slug=slug)
    db.session.add(org)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise UserServiceError("Organization slug already exists.", "duplicate_slug") from None

    from app.leads.services import seed_default_pipeline_stages

    seed_default_pipeline_stages(org.id)
    from app.automations.seed import seed_default_automations

    seed_default_automations(org.id)
    from app.streams.models import OrgLeadSettings

    db.session.add(OrgLeadSettings(organization_id=org.id))
    return org


def is_account_locked(user: User) -> bool:
    if user.locked_until is None:
        return False
    now = datetime.now(timezone.utc)
    locked = user.locked_until
    if locked.tzinfo is None:
        locked = locked.replace(tzinfo=timezone.utc)
    return now < locked


def record_failed_login(user: User) -> None:
    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
    max_attempts = current_app.config.get("MAX_FAILED_LOGIN_ATTEMPTS", 5)
    if user.failed_login_attempts >= max_attempts:
        from datetime import timedelta

        minutes = current_app.config.get("LOGIN_LOCKOUT_MINUTES", 15)
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)


def reset_login_attempts(user: User) -> None:
    user.failed_login_attempts = 0
    user.locked_until = None


def change_password(user: User, new_password: str, *, actor: User | None = None) -> None:
    ok, msg = validate_password(new_password)
    if not ok:
        raise UserServiceError(msg or "Invalid password.", "invalid_password")
    user.password_hash = hash_password(new_password)
    log_audit(
        "password_changed",
        user_id=actor.id if actor else user.id,
        organization_id=user.organization_id,
        target_type="user",
        target_id=user.id,
    )
