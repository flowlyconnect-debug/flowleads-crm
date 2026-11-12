"""Calendar OAuth and settings routes on settings_bp."""

from __future__ import annotations

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.calendar.providers import GoogleCalendarProvider, MicrosoftCalendarProvider
from app.calendar.services import CalendarService, CalendarServiceError
from app.core.permissions import require_role
from app.extensions import db


def _oauth_serializer():
    from flask import current_app

    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="calendar-oauth")


def provider_ready(provider: str) -> bool:
    """OAuth keys exist for the provider (GOOGLE_* / MICROSOFT_* env vars)."""
    from flask import current_app

    prefix = "GOOGLE" if provider == "google" else "MICROSOFT"
    return all(
        (current_app.config.get(f"{prefix}_{key}") or "").strip()
        for key in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI")
    )


def _oauth_failed(label: str, exc: Exception) -> None:
    """Log the technical reason, show the user a plain message."""
    from flask import current_app

    current_app.logger.warning("Calendar %s failed: %s", label, exc)
    flash(f"{label}-yhteys epäonnistui. Yritä uudelleen hetken kuluttua.", "danger")


def register_calendar_settings_routes(settings_bp):
    @settings_bp.route("/calendar", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin", "user")
    def calendar_settings():
        connection = CalendarService.get_active_connection(
            current_user.id, current_user.organization_id
        )
        return render_template(
            "settings/calendar.html",
            connection=connection,
            google_ready=provider_ready("google"),
            microsoft_ready=provider_ready("microsoft"),
        )

    @settings_bp.route("/calendar/connect/google", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin", "user")
    def calendar_connect_google():
        if not provider_ready("google"):
            flash("Google Calendar -liitos ei ole vielä käytössä tässä palvelussa.", "warning")
            return redirect(url_for("settings.calendar_settings"))
        signed = _oauth_serializer().dumps(
            {"user_id": current_user.id, "provider": "google"}
        )
        try:
            auth_url = GoogleCalendarProvider.get_authorization_url(signed)
        except Exception as exc:
            _oauth_failed("Google Calendar", exc)
            return redirect(url_for("settings.calendar_settings"))
        return redirect(auth_url)

    @settings_bp.route("/calendar/callback/google", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin", "user")
    def calendar_callback_google():
        error = request.args.get("error")
        if error:
            flash(f"Google-yhteys peruutettiin: {error}", "warning")
            return redirect(url_for("settings.calendar_settings"))

        code = request.args.get("code")
        state = request.args.get("state")
        if not code or not state:
            flash("Puuttuva OAuth-vastaus Googlelta.", "danger")
            return redirect(url_for("settings.calendar_settings"))

        try:
            payload = _oauth_serializer().loads(state, max_age=600)
        except (BadSignature, SignatureExpired):
            flash("OAuth-istunto vanhentui. Yritä uudelleen.", "danger")
            return redirect(url_for("settings.calendar_settings"))

        if payload.get("user_id") != current_user.id:
            flash("Virheellinen OAuth-istunto.", "danger")
            return redirect(url_for("settings.calendar_settings"))

        try:
            tokens = GoogleCalendarProvider.exchange_code(code)
            CalendarService.save_oauth_connection(
                current_user.id,
                current_user.organization_id,
                "google",
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token"),
                expires_at=tokens.get("expires_at"),
            )
            db.session.commit()
            flash("Google Calendar yhdistetty.", "success")
        except Exception as exc:
            db.session.rollback()
            _oauth_failed("Google Calendar", exc)
        return redirect(url_for("settings.calendar_settings"))

    @settings_bp.route("/calendar/connect/microsoft", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin", "user")
    def calendar_connect_microsoft():
        if not provider_ready("microsoft"):
            flash("Microsoft Outlook -liitos ei ole vielä käytössä tässä palvelussa.", "warning")
            return redirect(url_for("settings.calendar_settings"))
        signed = _oauth_serializer().dumps(
            {"user_id": current_user.id, "provider": "microsoft"}
        )
        try:
            auth_url = MicrosoftCalendarProvider.get_authorization_url(signed)
        except Exception as exc:
            _oauth_failed("Microsoft Outlook", exc)
            return redirect(url_for("settings.calendar_settings"))
        return redirect(auth_url)

    @settings_bp.route("/calendar/callback/microsoft", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin", "user")
    def calendar_callback_microsoft():
        error = request.args.get("error")
        if error:
            flash(f"Microsoft-yhteys peruutettiin: {error}", "warning")
            return redirect(url_for("settings.calendar_settings"))

        code = request.args.get("code")
        state = request.args.get("state")
        if not code or not state:
            flash("Puuttuva OAuth-vastaus Microsoftilta.", "danger")
            return redirect(url_for("settings.calendar_settings"))

        try:
            payload = _oauth_serializer().loads(state, max_age=600)
        except (BadSignature, SignatureExpired):
            flash("OAuth-istunto vanhentui. Yritä uudelleen.", "danger")
            return redirect(url_for("settings.calendar_settings"))

        if payload.get("user_id") != current_user.id:
            flash("Virheellinen OAuth-istunto.", "danger")
            return redirect(url_for("settings.calendar_settings"))

        try:
            tokens = MicrosoftCalendarProvider.exchange_code(code)
            CalendarService.save_oauth_connection(
                current_user.id,
                current_user.organization_id,
                "microsoft",
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token"),
                expires_at=tokens.get("expires_at"),
            )
            db.session.commit()
            flash("Microsoft Outlook yhdistetty.", "success")
        except Exception as exc:
            db.session.rollback()
            _oauth_failed("Microsoft Outlook", exc)
        return redirect(url_for("settings.calendar_settings"))

    @settings_bp.route("/calendar/disconnect", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin", "user")
    def calendar_disconnect():
        try:
            CalendarService.disconnect(current_user.id, current_user.organization_id)
            db.session.commit()
            flash("Kalenteriyhteys katkaistu.", "success")
        except Exception as exc:
            db.session.rollback()
            _oauth_failed("Kalenterin katkaisu", exc)
        return redirect(url_for("settings.calendar_settings"))

    @settings_bp.route("/calendar/test", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin", "user")
    def calendar_test():
        connection = CalendarService.get_active_connection(
            current_user.id, current_user.organization_id
        )
        if not connection:
            return jsonify({"success": False, "error": "no_connection", "message": "Kalenteria ei ole yhdistetty.", "calendars": []}), 400
        try:
            calendars = CalendarService.test_connection(connection)
            db.session.commit()
            return jsonify({"success": True, "calendars": calendars})
        except CalendarServiceError as exc:
            db.session.rollback()
            return jsonify({"success": False, "error": exc.code, "message": exc.message}), 400
        except Exception as exc:
            db.session.rollback()
            return jsonify({"success": False, "error": "provider_error", "message": "Kalenteripalvelu ei vastannut. Yritä myöhemmin uudelleen."}), 500
