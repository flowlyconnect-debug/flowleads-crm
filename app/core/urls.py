"""Absolute links that leave the app (e-mails, copied invite links)."""

from __future__ import annotations

from flask import current_app, url_for


def external_url(endpoint: str, **values) -> str:
    """Absolute URL for a link sent outside the app.

    Uses APP_BASE_URL when it is set, so the link always points at the public
    address, not at whatever host the request came in on (localhost when the
    app runs on a laptop against the production database, the internal Render
    host, an http:// address behind a proxy)."""
    path = url_for(endpoint, **values)
    base = (current_app.config.get("APP_BASE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}{path}"
    return url_for(endpoint, _external=True, **values)
