from flask import current_app, g, request

from app.extensions import limiter


def api_rate_limit_key() -> str:
    return getattr(g, "api_key_rate_key", request.remote_addr or "anonymous")


def api_rate_limit():
    # n8n job tokens post every relevant listing of a full-region run, which
    # can legitimately exceed the per-key limit meant for third-party clients.
    if getattr(g, "key_name", None) == "n8n-job-token":
        return current_app.config.get("N8N_JOB_RATE_LIMIT", "2000/hour")
    return current_app.config.get("API_RATE_LIMIT", "100/hour")
