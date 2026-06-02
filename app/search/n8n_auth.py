from __future__ import annotations

import secrets
from functools import wraps

from flask import current_app, jsonify, request


def require_n8n_secret(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = current_app.config.get("N8N_MASTER_SECRET")
        if not expected:
            return jsonify({"error": "n8n integration is not configured."}), 503

        provided = request.headers.get("X-N8N-Secret", "").strip()
        if not provided or not secrets.compare_digest(provided, expected):
            return jsonify({"error": "unauthorized"}), 401

        return view(*args, **kwargs)

    return wrapped
