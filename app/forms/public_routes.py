from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from app.extensions import db, limiter
from app.forms.services import WebFormService, WebFormServiceError, get_active_form_by_token

forms_public_api_bp = Blueprint("forms_public_api", __name__, url_prefix="/api/public/forms")


def _cors_headers(response: Response) -> Response:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
    return response


@forms_public_api_bp.after_request
def _add_cors(response):
    if request.path.startswith("/api/public/forms"):
        return _cors_headers(response)
    return response


@forms_public_api_bp.route("/<form_token>", methods=["OPTIONS"])
@forms_public_api_bp.route("/<form_token>/submit", methods=["OPTIONS"])
def cors_preflight(form_token: str):
    return _cors_headers(Response(status=204))


def _request_meta() -> dict:
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = (forwarded.split(",")[0].strip() if forwarded else None) or request.remote_addr
    return {
        "ip": ip,
        "user_agent": (request.user_agent.string or "")[:500],
    }


def _parse_submission_payload() -> dict:
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    if request.form:
        return request.form.to_dict(flat=True)
    return {}


def _public_json(payload: dict, status: int = 200):
    response = jsonify(payload)
    return _cors_headers(response), status


@forms_public_api_bp.route("/<form_token>", methods=["GET"])
@limiter.limit("120/hour")
def public_form_definition(form_token: str):
    form = get_active_form_by_token(form_token)
    if not form:
        return _public_json(
            {
                "success": False,
                "error": {"code": "not_found", "message": "Form not found."},
            },
            404,
        )
    return _public_json({"success": True, "data": WebFormService.public_form_dict(form)})


def _form_submit_rate_limit():
    from flask import current_app

    return current_app.config.get("FORM_SUBMISSION_RATE_LIMIT", "10/hour")


@forms_public_api_bp.route("/<form_token>/submit", methods=["POST"])
@limiter.limit(_form_submit_rate_limit)
def public_form_submit(form_token: str):
    try:
        result = WebFormService.submit(
            form_token,
            _parse_submission_payload(),
            _request_meta(),
        )
        db.session.commit()
        status = 200 if result.get("success") else 400
        return _public_json(result, status)
    except WebFormServiceError:
        db.session.rollback()
        return _public_json(
            {
                "success": False,
                "error": {"code": "not_found", "message": "Form not found."},
            },
            404,
        )
    except Exception:
        db.session.rollback()
        return _public_json(
            {
                "success": False,
                "error": {
                    "code": "server_error",
                    "message": "Unable to process your submission. Please try again later.",
                },
            },
            500,
        )
