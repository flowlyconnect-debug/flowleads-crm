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
    enable_totp,
    logout_user_session,
    setup_totp,
    verify_totp_login,
)
from app.auth.totp_utils import normalize_totp_code
from app.core.audit import log_audit
from app.core.permissions import is_2fa_verified, require_2fa
from app.core.security import (
    generate_password_reset_token,
    verify_password_reset_token,
)
from app.extensions import db, limiter
from app.users.services import (
    UserServiceError,
    change_password,
    get_user_by_email,
)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


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
            if user.is_superadmin():
                if not user.totp_enabled:
                    return redirect(url_for("auth.two_fa_setup"))
                if not is_2fa_verified():
                    return redirect(url_for("auth.two_fa_verify"))
            next_url = request.args.get("next")
            if next_url:
                return redirect(next_url)
            return redirect(url_for("analytics.dashboard"))

    return render_template("auth/login.html", form=form)


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
def reset_password_request():
    form = ResetPasswordRequestForm()
    if form.validate_on_submit():
        user = get_user_by_email(form.email.data)
        if user and user.can_login_ui():
            token = generate_password_reset_token(user.email)
            flash(
                "If an account exists for that email, you will receive reset instructions.",
                "info",
            )
            from flask import current_app

            reset_url = url_for("auth.reset_password", token=token, _external=True)
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
    email = verify_password_reset_token(token)
    form = ResetPasswordForm()
    if email is None:
        flash("The reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.reset_password_request"))

    user = get_user_by_email(email)
    if user is None or not user.can_login_ui():
        flash("The reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.reset_password_request"))

    if form.validate_on_submit():
        if form.password.data != form.password_confirm.data:
            flash("Passwords do not match.", "danger")
        else:
            try:
                change_password(user, form.password.data)
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
    if not current_user.is_superadmin():
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
            flash("Two-factor authentication has been enabled.", "success")
            return render_template(
                "auth/two_fa_backup_codes.html",
                backup_codes=raw_codes,
            )
        except UserServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")

    return render_template("auth/two_fa_setup.html", form=form, qr_code=qr_b64)


@auth_bp.route("/2fa/verify", methods=["GET", "POST"])
@login_required
def two_fa_verify():
    if not current_user.is_superadmin() or not current_user.totp_enabled:
        return redirect(url_for("analytics.dashboard"))

    if is_2fa_verified():
        return redirect(url_for("analytics.dashboard"))

    form = TwoFAVerifyForm()
    if form.validate_on_submit():
        token = normalize_totp_code(form.token.data)
        backup = (
            form.backup_code.data.strip() if form.backup_code.data else None
        )
        if not token and not backup:
            flash("Enter a verification code or backup code.", "danger")
        elif verify_totp_login(current_user, token, backup):
            complete_2fa_session(current_user)
            flash("Verification successful.", "success")
            return redirect(url_for("analytics.dashboard"))
        else:
            flash("Invalid verification code.", "danger")

    return render_template("auth/two_fa_verify.html", form=form)
