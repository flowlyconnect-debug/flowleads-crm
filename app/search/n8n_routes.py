from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.search.n8n_auth import require_n8n_secret
from app.search.services import (
    check_dedupe_for_n8n,
    list_jobs_for_n8n,
    mark_dedupe_for_n8n,
    update_job_for_n8n,
)

n8n_bp = Blueprint("n8n_search", __name__, url_prefix="/api/v1/n8n")


def _json_error(message: str, status: int):
    return jsonify({"error": message}), status


def _parse_json_body() -> dict | None:
    if not request.is_json:
        return None
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@n8n_bp.route("/jobs", methods=["GET"])
@require_n8n_secret
def list_jobs():
    status = (request.args.get("status") or "pending").strip()
    limit = request.args.get("limit", default=10, type=int)
    if limit is None or limit < 1:
        limit = 10
    limit = min(limit, 100)

    try:
        jobs = list_jobs_for_n8n(status=status, limit=limit)
    except ValueError as exc:
        return _json_error(str(exc), 400)

    return jsonify({"jobs": jobs})


@n8n_bp.route("/jobs/<int:job_id>", methods=["PATCH"])
@require_n8n_secret
def patch_job(job_id: int):
    body = _parse_json_body()
    if body is None:
        return _json_error("JSON body is required.", 400)

    status = body.get("status")
    if not status or not isinstance(status, str):
        return _json_error("status is required.", 400)

    leads_found = body.get("leads_found")
    leads_sent = body.get("leads_sent")
    error = body.get("error")

    if leads_found is not None and not isinstance(leads_found, int):
        return _json_error("leads_found must be an integer.", 400)
    if leads_sent is not None and not isinstance(leads_sent, int):
        return _json_error("leads_sent must be an integer.", 400)
    if error is not None and not isinstance(error, str):
        return _json_error("error must be a string.", 400)

    try:
        job = update_job_for_n8n(
            job_id,
            status=status.strip(),
            leads_found=leads_found,
            leads_sent=leads_sent,
            error=error,
        )
    except ValueError as exc:
        return _json_error(str(exc), 400)

    if job is None:
        return _json_error("job not found.", 404)

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _json_error("unable to update job.", 500)

    return jsonify(
        {
            "job_id": job.id,
            "status": job.status,
            "leads_found": job.leads_found,
            "leads_sent": job.leads_sent,
            "error": job.error_message or "",
        }
    )


@n8n_bp.route("/dedupe/check", methods=["POST"])
@require_n8n_secret
def dedupe_check():
    body = _parse_json_body()
    if body is None:
        return _json_error("JSON body is required.", 400)

    organization_id = body.get("organization_id")
    source_ids = body.get("source_ids")
    if organization_id is None or not isinstance(organization_id, int):
        return _json_error("organization_id is required.", 400)
    if not isinstance(source_ids, list) or not all(isinstance(s, str) for s in source_ids):
        return _json_error("source_ids must be a list of strings.", 400)

    result = check_dedupe_for_n8n(organization_id, source_ids)
    return jsonify(result)


@n8n_bp.route("/dedupe/mark", methods=["POST"])
@require_n8n_secret
def dedupe_mark():
    body = _parse_json_body()
    if body is None:
        return _json_error("JSON body is required.", 400)

    organization_id = body.get("organization_id")
    source_ids = body.get("source_ids")
    if organization_id is None or not isinstance(organization_id, int):
        return _json_error("organization_id is required.", 400)
    if not isinstance(source_ids, list) or not all(isinstance(s, str) for s in source_ids):
        return _json_error("source_ids must be a list of strings.", 400)

    try:
        marked = mark_dedupe_for_n8n(organization_id, source_ids)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return _json_error("unable to mark source ids.", 500)

    return jsonify({"marked": marked})
