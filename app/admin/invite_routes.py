"""Public (unauthenticated) customer invite acceptance."""

from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.admin.constants import FEATURE_CATALOG
from app.admin.invite_services import InviteServiceError, get_valid_invite
from app.admin.invite_services import accept_invite as accept_invite_service
from app.core.security import validate_password
from app.extensions import db

invite_bp = Blueprint("invite", __name__, url_prefix="/invite")


@invite_bp.route("/<token>", methods=["GET", "POST"])
def accept_invite(token: str):
    invite = get_valid_invite(token)
    if invite is None:
        return render_template("admin/invite_accept.html", invite=None)

    allowed_features = [
        (key, FEATURE_CATALOG[key]) for key in (invite.allowed_features or []) if key in FEATURE_CATALOG
    ]

    if request.method == "POST":
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        enabled_features = request.form.getlist("enabled_features")

        if password != password_confirm:
            flash("Salasanat eivät täsmää.", "danger")
        else:
            ok, msg = validate_password(password)
            if not ok:
                flash(msg or "Salasana ei kelpaa.", "danger")
            else:
                # No region choice: the profile covers the whole country and
                # the customer narrows the list on the Kohteet page.
                try:
                    accept_invite_service(
                        invite,
                        password=password,
                        enabled_features=enabled_features,
                    )
                    db.session.commit()
                    flash(
                        "Tili on käyttövalmis. Kirjaudu sisään sähköpostiosoitteellasi ja "
                        "uudella salasanallasi.",
                        "success",
                    )
                    return redirect(url_for("auth.login"))
                except InviteServiceError as exc:
                    db.session.rollback()
                    flash(exc.message, "danger")

    return render_template(
        "admin/invite_accept.html",
        invite=invite,
        allowed_features=allowed_features,
    )
