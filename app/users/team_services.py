"""Org-level teammate management: invite, resend invite, activate/deactivate.

Reuses the existing password-reset token mechanism to let an admin add a
colleague to their own (already-onboarded) organization without the admin
ever handling or choosing that person's password.
"""
import secrets

from app.extensions import db
from app.users.models import Organization, User
from app.users.services import UserServiceError, create_user, ensure_same_organization

TEAM_INVITE_ROLES = ("user", "admin")


def list_team_members(organization_id: int) -> list[User]:
    return (
        User.query.filter_by(organization_id=organization_id)
        .order_by(User.email.asc())
        .all()
    )


def invite_teammate(organization_id: int, email: str, role: str, *, actor: User) -> User:
    if role not in TEAM_INVITE_ROLES:
        raise UserServiceError("Invalid role.", "invalid_role")

    throwaway_password = secrets.token_urlsafe(32)
    user = create_user(
        email,
        throwaway_password,
        role=role,
        organization_id=organization_id,
        actor=actor,
    )
    db.session.flush()
    _send_invite(user)
    return user


def resend_teammate_invite(user: User, *, actor: User) -> None:
    ensure_same_organization(actor, user)
    _send_invite(user)


def _send_invite(user: User) -> None:
    from flask import url_for

    from app.auth.services import issue_password_reset_token
    from app.email.services import EmailService

    token = issue_password_reset_token(user)
    set_password_url = url_for("auth.reset_password", token=token, _external=True)
    organization = db.session.get(Organization, user.organization_id)
    EmailService.send_teammate_invite_email(
        to_email=user.email,
        set_password_url=set_password_url,
        organization_name=organization.name if organization else "FlowLeads",
    )
