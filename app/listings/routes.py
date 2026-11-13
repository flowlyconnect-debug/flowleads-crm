"""n8n-facing API for the listing catalogue. All routes require the
X-N8N-Secret header (same as /api/v1/n8n/jobs)."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.extensions import db
from app.listings.import_services import import_listings
from app.listings.services import (
    ListingServiceError,
    claim_analysis,
    claim_contacts,
    get_crawl_plan,
    ingest_page,
    save_analysis,
    save_contacts,
)
from app.search.n8n_auth import require_n8n_secret

listings_n8n_bp = Blueprint("listings_n8n", __name__, url_prefix="/api/v1/n8n/listings")


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _body() -> dict | None:
    if not request.is_json:
        return None
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _int(value, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _run(fn, *args, **kwargs):
    try:
        return jsonify(fn(*args, **kwargs))
    except ListingServiceError as exc:
        db.session.rollback()
        status = 404 if exc.message == "listing not found" else 400
        return _error(exc.message, status)


@listings_n8n_bp.route("/crawl-plan", methods=["GET"])
@require_n8n_secret
def crawl_plan():
    return jsonify({"tasks": get_crawl_plan()})


@listings_n8n_bp.route("/ingest", methods=["POST"])
@require_n8n_secret
def ingest():
    body = _body()
    if body is None:
        return _error("JSON body is required.")
    return _run(
        ingest_page,
        maakunta=str(body.get("maakunta") or ""),
        mode=str(body.get("mode") or "incremental"),
        offset=max(0, _int(body.get("offset"))),
        page_in_run=max(1, _int(body.get("page_in_run"), 1)),
        listings=body.get("listings") if body.get("listings") is not None else [],
        error=body.get("error") or None,
    )


@listings_n8n_bp.route("/import", methods=["POST"])
@require_n8n_secret
def import_route():
    """Listings from any source other than the Oikotie crawler (a licensed
    feed, a partner export). Same row format as the admin CSV import."""
    body = _body()
    if body is None:
        return _error("JSON body is required.")
    return _run(import_listings, body.get("listings"), source=str(body.get("source") or "import"))


@listings_n8n_bp.route("/managers/import", methods=["POST"])
@require_n8n_secret
def managers_import_route():
    """Property management offices: {"offices": [{"name", "ytunnus", "osoite",
    "postinumero", "kaupunki", "puhelin", "email", "website"}], "source": "..."}."""
    from app.listings.contacts import OfficeImportError, import_offices

    body = _body()
    if body is None:
        return _error("JSON body is required.")
    try:
        return jsonify(import_offices(body.get("offices"), source=str(body.get("source") or "import")[:50]))
    except OfficeImportError as exc:
        db.session.rollback()
        return _error(exc.message)


@listings_n8n_bp.route("/analysis/claim", methods=["POST"])
@require_n8n_secret
def analysis_claim():
    body = _body() or {}
    return jsonify(claim_analysis(limit=_int(body.get("limit"), 20)))


@listings_n8n_bp.route("/<int:listing_id>/analysis", methods=["POST"])
@require_n8n_secret
def analysis_save(listing_id: int):
    body = _body()
    if body is None:
        return _error("JSON body is required.")
    return _run(save_analysis, listing_id, body)


@listings_n8n_bp.route("/contacts/claim", methods=["POST"])
@require_n8n_secret
def contacts_claim():
    body = _body() or {}
    return jsonify(claim_contacts(limit=_int(body.get("limit"), 10)))


@listings_n8n_bp.route("/<int:listing_id>/contacts", methods=["POST"])
@require_n8n_secret
def contacts_save(listing_id: int):
    body = _body()
    if body is None:
        return _error("JSON body is required.")
    return _run(save_contacts, listing_id, body)
