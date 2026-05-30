"""Temporary production diagnostics for 500 errors."""

from __future__ import annotations

import logging
import sys
import traceback
from typing import Any

from flask import Flask, current_app, has_request_context, request
from flask_login import current_user
from werkzeug.exceptions import HTTPException

_last_error: dict[str, Any] | None = None


def diagnostics_enabled(app: Flask | None = None) -> bool:
    app = app or current_app
    if app.config.get("FLASK_ENV") != "production":
        return True
    return bool(app.config.get("DEBUG_DIAGNOSTICS"))


def get_last_error() -> dict[str, Any] | None:
    if _last_error is None:
        return None
    return dict(_last_error)


def clear_last_error() -> None:
    global _last_error
    _last_error = None


def _resolve_exception(error: BaseException) -> BaseException:
    if isinstance(error, HTTPException) and error.original_exception is not None:
        return error.original_exception
    return error


def _request_context() -> dict[str, Any]:
    if not has_request_context():
        return {
            "path": None,
            "method": None,
            "args": {},
            "user_id": None,
            "organization_id": None,
        }

    args = request.args.to_dict(flat=False)
    user_id = None
    organization_id = request.args.get("organization_id") or request.form.get("organization_id")

    try:
        if current_user.is_authenticated:
            user_id = getattr(current_user, "id", None)
            if organization_id in (None, ""):
                organization_id = getattr(current_user, "organization_id", None)
    except Exception:
        pass

    return {
        "path": request.path,
        "method": request.method,
        "args": args,
        "user_id": user_id,
        "organization_id": organization_id,
    }


def capture_server_error(
    error: BaseException,
    *,
    hint: str | None = None,
    status_code: int = 500,
) -> dict[str, Any]:
    """Log a detailed 500 record to stdout and store it for /api/v1/debug/last-error."""
    global _last_error

    exc = _resolve_exception(error)
    tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    ctx = _request_context()

    record = {
        "path": ctx["path"],
        "method": ctx["method"],
        "args": ctx["args"],
        "user_id": ctx["user_id"],
        "organization_id": ctx["organization_id"],
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "traceback": tb_str,
        "hint": hint,
        "status_code": status_code,
    }
    _last_error = record

    current_app.logger.error(
        "DIAG_500 path=%s method=%s args=%r user_id=%s organization_id=%s "
        "error_type=%s error_message=%r hint=%r\n%s",
        record["path"],
        record["method"],
        record["args"],
        record["user_id"],
        record["organization_id"],
        record["error_type"],
        record["error_message"],
        record["hint"],
        tb_str,
    )
    return record


def configure_stdout_logging(app: Flask) -> None:
    """Ensure application logs are written to stdout for Render log streaming."""
    if app.config.get("TESTING"):
        return

    level = logging.DEBUG if app.debug else logging.INFO
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s in %(name)s: %(message)s")
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(formatter)

    app.logger.handlers.clear()
    app.logger.setLevel(level)
    app.logger.propagate = False
    app.logger.addHandler(handler)

    for logger_name in ("werkzeug",):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
