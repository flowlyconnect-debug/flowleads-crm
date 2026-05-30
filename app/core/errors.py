from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_limiter.errors import RateLimitExceeded
from flask_login import current_user
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.core.diagnostics import capture_server_error
from app.extensions import db


def wants_json_response() -> bool:
    if request.path.startswith("/api"):
        return True
    accept = request.accept_mimetypes
    return accept.best == "application/json" and accept["application/json"] >= accept["text/html"]


def json_error(code: str, message: str, status: int):
    return (
        jsonify(
            {
                "success": False,
                "data": None,
                "error": {"code": code, "message": message},
            }
        ),
        status,
    )


def json_success(data=None, status: int = 200):
    return (
        jsonify(
            {
                "success": True,
                "data": data,
                "error": None,
            }
        ),
        status,
    )


def _log_server_error(error, *, hint: str | None = None, status_code: int = 500) -> None:
    capture_server_error(error, hint=hint, status_code=status_code)


def register_error_handlers(app):
    @app.errorhandler(ProgrammingError)
    @app.errorhandler(OperationalError)
    def handle_database_error(error):
        db.session.rollback()
        _log_server_error(
            error,
            hint="Database schema/query failure — ensure `flask db upgrade` has been applied",
        )
        if wants_json_response():
            return json_error("database_error", "A database error occurred.", 500)
        return render_template("errors/500.html"), 500

    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit_exceeded(error: RateLimitExceeded):
        if wants_json_response():
            code = "rate_limit_exceeded" if request.path.startswith("/api") else "rate_limited"
            message = (
                "API rate limit exceeded. Please try again later."
                if code == "rate_limit_exceeded"
                else "Too many requests. Please try again later."
            )
            response, status = json_error(code, message, 429)
            retry_after = error.retry_after if error.retry_after is not None else 60
            response.headers["Retry-After"] = str(int(retry_after))
            return response, status
        return render_template("errors/429.html"), 429

    @app.errorhandler(400)
    def bad_request(error):
        if wants_json_response():
            return json_error("bad_request", "The request could not be understood.", 400)
        return render_template("errors/400.html"), 400

    @app.errorhandler(401)
    def unauthorized(error):
        if wants_json_response():
            return json_error("unauthorized", "Authentication is required.", 401)
        return render_template("errors/401.html"), 401

    @app.errorhandler(403)
    def forbidden(error):
        if wants_json_response():
            return json_error("forbidden", "You do not have permission to perform this action.", 403)
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        if (
            not wants_json_response()
            and current_user.is_authenticated
            and current_user.is_superadmin()
            and getattr(error, "description", None) == "Organization not found."
        ):
            flash("Valittua organisaatiota ei löytynyt. Valitse organisaatio dashboardilta.", "warning")
            return redirect(url_for("analytics.dashboard"))
        if wants_json_response():
            return json_error("not_found", "The requested resource was not found.", 404)
        return render_template("errors/404.html"), 404

    @app.errorhandler(429)
    def rate_limited(error):
        if wants_json_response():
            code = "rate_limit_exceeded" if request.path.startswith("/api") else "rate_limited"
            message = (
                "API rate limit exceeded. Please try again later."
                if code == "rate_limit_exceeded"
                else "Too many requests. Please try again later."
            )
            response, status = json_error(code, message, 429)
            retry_after = getattr(error, "retry_after", None) or getattr(
                getattr(error, "description", None), "retry_after", None
            )
            if retry_after is None and hasattr(error, "description"):
                retry_after = getattr(error.description, "retry_after", None)
            from flask_limiter.errors import RateLimitExceeded

            if isinstance(error, RateLimitExceeded):
                retry_after = error.retry_after
            if retry_after:
                response.headers["Retry-After"] = str(int(retry_after))
            return response, status
        return render_template("errors/429.html"), 429

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        _log_server_error(error)
        if wants_json_response():
            code = "server_error" if request.path.startswith("/api") else "internal_error"
            return json_error(code, "An unexpected error occurred.", 500)
        return render_template("errors/500.html"), 500
