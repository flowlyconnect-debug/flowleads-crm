from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.permissions import require_2fa, require_role
from app.extensions import db
from app.users.models import Organization, User
from app.users.services import UserServiceError, set_user_active
from app.users.team_services import (
    TEAM_INVITE_ROLES,
    invite_teammate,
    list_team_members,
    resend_teammate_invite,
)


def _resolve_organization_id():
    if current_user.is_superadmin():
        from app.core.tenant import resolve_organization_id

        return resolve_organization_id()
    return current_user.organization_id


def _redirect_to_team(organization_id):
    kwargs = {"organization_id": organization_id} if current_user.is_superadmin() else {}
    return redirect(url_for("settings.team_members", **kwargs))


def _load_team_target(user_id: int) -> User:
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)
    if not current_user.is_superadmin() and target.organization_id != current_user.organization_id:
        abort(404)
    return target


def register_team_settings_routes(settings_bp):
    @settings_bp.route("/team", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin")
    @require_2fa
    def team_members():
        organization_id = _resolve_organization_id()
        if organization_id is None:
            abort(403)
        if db.session.get(Organization, organization_id) is None:
            abort(404)

        members = list_team_members(organization_id)
        return render_template(
            "settings/team.html",
            members=members,
            organization_id=organization_id,
            invite_roles=TEAM_INVITE_ROLES,
        )

    @settings_bp.route("/team/invite", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    @require_2fa
    def invite_team_member():
        organization_id = _resolve_organization_id()
        if organization_id is None:
            flash("Organisaatio puuttuu.", "danger")
            return redirect(url_for("settings.team_members"))

        email = (request.form.get("email") or "").strip()
        role = request.form.get("role") or "user"
        try:
            invite_teammate(organization_id, email, role, actor=current_user)
            db.session.commit()
            flash("Kutsu lähetetty.", "success")
        except UserServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
        return _redirect_to_team(organization_id)

    @settings_bp.route("/team/<int:user_id>/resend-invite", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    @require_2fa
    def resend_team_invite(user_id):
        target = _load_team_target(user_id)
        try:
            resend_teammate_invite(target, actor=current_user)
            db.session.commit()
            flash("Kutsu lähetetty uudelleen.", "success")
        except UserServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
        return _redirect_to_team(target.organization_id)

    @settings_bp.route("/team/<int:user_id>/deactivate", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    @require_2fa
    def deactivate_team_member(user_id):
        target = _load_team_target(user_id)
        try:
            set_user_active(target, False, actor=current_user)
            db.session.commit()
            flash("Käyttäjä poistettu käytöstä.", "success")
        except UserServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
        return _redirect_to_team(target.organization_id)

    @settings_bp.route("/team/<int:user_id>/reactivate", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    @require_2fa
    def reactivate_team_member(user_id):
        target = _load_team_target(user_id)
        try:
            set_user_active(target, True, actor=current_user)
            db.session.commit()
            flash("Käyttäjä aktivoitu.", "success")
        except UserServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
        return _redirect_to_team(target.organization_id)
