from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import SQLAlchemyError

from app.auth.forms import (
    LoginForm,
    RegisterForm,
    ResetPasswordForm,
    ResetPasswordRequestForm,
    TwoFASetupForm,
    TwoFAVerifyForm,
)
from app.auth.services import (
    authenticate_user,
    complete_2fa_session,
    consume_password_reset_token,
    enable_totp,
    get_valid_password_reset_token,
    issue_password_reset_token,
    logout_user_session,
    setup_totp,
    verify_totp_login,
)
from app.auth.totp_utils import normalize_totp_code
from app.core.audit import log_audit
from app.core.permissions import is_2fa_verified, require_2fa
from app.email.services import EmailService
from app.extensions import db, limiter
from app.users.services import (
    UserServiceError,
    change_password,
    get_user_by_email,
    get_user_by_id,
)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.before_app_request
def _enforce_two_factor():
    """Mandatory 2FA (admin, superadmin) applies to the WHOLE app, not just
    to views that carry @require_2fa: a signed-in user who has not set up or
    not passed the second factor can reach only the auth pages. Without this,
    typing e.g. /leads/ after the password step skipped 2FA entirely.

    Registered before the password hook on purpose: 2FA first, then the
    temporary-password change."""
    from flask import current_app, jsonify

    if not current_app.config.get("TWO_FA_ENFORCE_ALL_PAGES", True):
        return None
    if not current_user.is_authenticated or not current_user.requires_mandatory_2fa():
        return None
    endpoint = request.endpoint or ""
    if endpoint == "static" or endpoint.startswith("auth."):
        return None
    if current_user.totp_enabled and is_2fa_verified():
        return None
    wants_json = request.path.startswith("/api/") or request.is_json
    if wants_json or request.method not in ("GET", "HEAD"):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Kaksivaiheinen tunnistautuminen vaaditaan.",
                    "code": "two_factor_required",
                }
            ),
            403,
        )
    if not current_user.totp_enabled:
        return redirect(url_for("auth.two_fa_setup"))
    return redirect(url_for("auth.two_fa_verify"))


@auth_bp.before_app_request
def _force_password_change():
    """A user created with a temporary password (User.must_change_password)
    can do nothing in the app before choosing their own. The auth pages stay
    reachable so login, 2FA and logout keep working."""
    if not current_user.is_authenticated or not getattr(current_user, "must_change_password", False):
        return None
    endpoint = request.endpoint or ""
    if endpoint == "static" or endpoint.startswith("auth."):
        return None
    if request.path.startswith("/api/"):
        return None
    return redirect(url_for("auth.change_password_route"))


# Pages a signed-in admin may open before finishing two-factor sign-in.
_TWO_FA_EXEMPT_PREFIXES = (
    "auth.",
    "invite.",
    "proposals_public.",
    "forms_public",
    "api.",  # API keys
    "n8n_search.",
    "listings_n8n.",
    "webhooks.",  # Mailgun events
)


@auth_bp.before_app_request
def _force_two_factor():
    """Admins and superadmins must complete 2FA before ANY page. This used to
    be a per-view decorator, and views without it (search profiles, proposal
    and calendar settings, Kohteet, leads...) opened with the password alone."""
    if not current_user.is_authenticated or not current_user.requires_mandatory_2fa():
        return None
    from app.core.permissions import is_2fa_verified

    if is_2fa_verified():
        return None
    endpoint = request.endpoint or ""
    if endpoint == "static" or endpoint.startswith(_TWO_FA_EXEMPT_PREFIXES):
        return None
    if request.is_json or request.accept_mimetypes.best == "application/json":
        from flask import jsonify

        return jsonify({"success": False, "error": {"code": "two_factor_required", "message": "Two-factor sign-in required."}}), 401
    if not current_user.totp_enabled:
        return redirect(url_for("auth.two_fa_setup"))
    return redirect(url_for("auth.two_fa_verify", next=request.full_path if request.method == "GET" else None))


@auth_bp.app_context_processor
def _inject_feature_helpers():
    from app.core.features import current_user_has_feature

    return {"has_feature": current_user_has_feature}


def _is_safe_next(target: str | None) -> bool:
    """Only same-site relative paths: /leads/3 yes, //evil.example or
    https://evil.example no."""
    if not target or not target.startswith("/"):
        return False
    return not (target.startswith("//") or target.startswith("/\\") or "\n" in target or "\r" in target)


def _login_rate_limit():
    from flask import current_app

    return current_app.config.get("LOGIN_RATE_LIMIT", "5/minute")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(_login_rate_limit)
def login():
    if current_user.is_authenticated:
        return redirect(url_for("analytics.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user, error = authenticate_user(form.email.data, form.password.data)
        if user is None:
            log_audit(
                "login_failed",
                metadata={"email": (form.email.data or "").strip().lower()[:255]},
            )
            db.session.commit()
            flash(error or "Invalid email or password.", "danger")
        else:
            login_user(user)
            log_audit(
                "login_success",
                user_id=user.id,
                organization_id=user.organization_id,
            )
            db.session.commit()
            if user.requires_mandatory_2fa():
                if not user.totp_enabled:
                    return redirect(url_for("auth.two_fa_setup"))
                if not is_2fa_verified():
                    return redirect(url_for("auth.two_fa_verify"))
            if user.must_change_password:
                return redirect(url_for("auth.change_password_route"))
            next_url = request.args.get("next")
            if _is_safe_next(next_url):
                return redirect(next_url)
            return redirect(url_for("analytics.dashboard"))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password_route():
    form = ResetPasswordForm()
    forced = bool(current_user.must_change_password)
    if form.validate_on_submit():
        if form.password.data != form.password_confirm.data:
            flash("Salasanat eivät täsmää.", "danger")
        else:
            try:
                change_password(current_user, form.password.data)
                db.session.commit()
                flash("Salasana vaihdettu.", "success")
                return redirect(url_for("analytics.dashboard"))
            except UserServiceError as exc:
                db.session.rollback()
                flash(exc.message, "danger")
            except SQLAlchemyError:
                db.session.rollback()
                flash("Salasanan vaihto epäonnistui. Yritä uudelleen.", "danger")
    elif request.method == "POST":
        flash("Salasanan pitää olla vähintään 12 merkkiä.", "danger")
    return render_template("auth/change_password.html", form=form, forced=forced)


@auth_bp.route("/logout")
@login_required
def logout():
    user_id = current_user.id
    org_id = current_user.organization_id
    logout_user_session()
    logout_user()
    log_audit("logout", user_id=user_id, organization_id=org_id)
    db.session.commit()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    from flask import current_app

    if not current_app.config.get("PUBLIC_REGISTRATION_ENABLED"):
        flash("Registration is not available.", "warning")
        return redirect(url_for("auth.login"))

    form = RegisterForm()
    if form.validate_on_submit():
        if form.password.data != form.password_confirm.data:
            flash("Passwords do not match.", "danger")
        else:
            flash("Registration is disabled.", "warning")
    return render_template("auth/register.html", form=form)


@auth_bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit(_login_rate_limit, methods=["POST"])
def reset_password_request():
    form = ResetPasswordRequestForm()
    if form.validate_on_submit():
        user = get_user_by_email(form.email.data)
        if user and user.can_login_ui():
            token = issue_password_reset_token(user)
            db.session.commit()
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            EmailService.send_password_reset_email(to_email=user.email, reset_url=reset_url)
            flash(
                "If an account exists for that email, you will receive reset instructions.",
                "info",
            )

            from flask import current_app

            if current_app.debug:
                flash(f"Reset link (dev only): {reset_url}", "secondary")
        else:
            flash(
                "If an account exists for that email, you will receive reset instructions.",
                "info",
            )
    return render_template("auth/reset_password.html", form=form, token=None)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    token_record = get_valid_password_reset_token(token)
    form = ResetPasswordForm()
    if token_record is None:
        flash("The reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.reset_password_request"))

    user = get_user_by_id(token_record.user_id)
    if user is None or not user.can_login_ui():
        flash("The reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.reset_password_request"))

    if form.validate_on_submit():
        if form.password.data != form.password_confirm.data:
            flash("Passwords do not match.", "danger")
        else:
            try:
                change_password(user, form.password.data)
                consume_password_reset_token(token_record)
                db.session.commit()
                flash("Your password has been updated.", "success")
                return redirect(url_for("auth.login"))
            except UserServiceError as exc:
                db.session.rollback()
                flash(exc.message, "danger")
            except SQLAlchemyError:
                db.session.rollback()
                flash("Unable to reset password. Please try again.", "danger")

    return render_template("auth/reset_password.html", form=form, token=token)


@auth_bp.route("/2fa/setup", methods=["GET", "POST"])
@login_required
def two_fa_setup():
    if not current_user.requires_mandatory_2fa():
        return redirect(url_for("analytics.dashboard"))

    form = TwoFASetupForm()
    qr_b64 = None
    secret = current_user.totp_secret

    if not secret and request.method == "GET":
        secret, _uri, qr_b64 = setup_totp(current_user)
    elif secret and not current_user.totp_enabled:
        from app.auth.services import get_totp_uri, generate_qr_code_base64

        qr_b64 = generate_qr_code_base64(get_totp_uri(current_user, secret))

    if form.validate_on_submit():
        try:
            raw_codes = enable_totp(
                current_user, normalize_totp_code(form.token.data) or ""
            )
            complete_2fa_session(current_user)
            return render_template(
                "auth/two_fa_backup_codes.html",
                backup_codes=raw_codes,
            )
        except UserServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")

    return render_template(
        "auth/two_fa_setup.html", form=form, qr_code=qr_b64, secret=current_user.totp_secret
    )


@auth_bp.route("/2fa/verify", methods=["GET", "POST"])
@login_required
@limiter.limit(_login_rate_limit, methods=["POST"])
def two_fa_verify():
    if not current_user.requires_mandatory_2fa() or not current_user.totp_enabled:
        return redirect(url_for("analytics.dashboard"))

    if is_2fa_verified():
        return redirect(url_for("analytics.dashboard"))

    form = TwoFAVerifyForm()
    if form.validate_on_submit():
        token = normalize_totp_code(form.token.data)
        if verify_totp_login(current_user, token):
            complete_2fa_session(current_user)
            if len(token or "") > 8:  # signed in with a backup code
                from app.auth.services import remaining_backup_codes

                left = remaining_backup_codes(current_user)
                flash(
                    f"Kirjauduit varakoodilla. Käyttämättömiä varakoodeja on jäljellä {left}.",
                    "warning" if left <= 2 else "info",
                )
            return redirect(url_for("analytics.dashboard"))
        flash("Invalid verification code.", "danger")

    return render_template("auth/two_fa_verify.html", form=form)
