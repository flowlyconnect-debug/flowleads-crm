from flask import current_app, g, request

from app.extensions import limiter


def api_rate_limit_key() -> str:
    return getattr(g, "api_key_rate_key", request.remote_addr or "anonymous")


def api_rate_limit():
    return current_app.config.get("API_RATE_LIMIT", "100/hour")
