"""Mailgun Domains API client.

Lets an organization send sales email from its own domain (so the From
header and the Mailgun envelope domain align for SPF/DKIM/DMARC), instead of
every organization sharing FlowLeads' own domain. Isolated in its own module
so it can be mocked cleanly in tests — these are real outbound HTTP calls to
Mailgun, never exercised against the real API in this codebase's tests.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

MAILGUN_API_BASE = "https://api.mailgun.net/v3"


class MailgunDomainError(Exception):
    def __init__(self, message: str, code: str = "mailgun_domain_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _auth_header() -> str:
    api_key = current_app.config.get("MAILGUN_API_KEY")
    if not api_key:
        raise MailgunDomainError("Mailgun is not configured (MAILGUN_API_KEY missing).", "not_configured")
    token = base64.b64encode(f"api:{api_key}".encode()).decode("ascii")
    return f"Basic {token}"


def _request(method: str, path: str, data: dict | None = None) -> dict:
    url = f"{MAILGUN_API_BASE}/{path}"
    if method == "GET" and data:
        url = f"{url}?{urllib.parse.urlencode(data)}"
        body = None
    else:
        body = urllib.parse.urlencode(data).encode("utf-8") if data else None

    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Authorization", _auth_header())

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads(exc.read().decode("utf-8", errors="replace"))
            message = err_body.get("message") or err_body.get("Error") or f"Mailgun HTTP {exc.code}"
        except (json.JSONDecodeError, ValueError):
            message = f"Mailgun HTTP {exc.code}"
        raise MailgunDomainError(message, "mailgun_api_error") from exc
    except urllib.error.URLError as exc:
        raise MailgunDomainError(f"Mailgun request failed: {exc.reason}", "network_error") from exc


def add_domain(domain: str) -> dict:
    """Register a new sending domain with Mailgun.

    Returns Mailgun's domain record, including `sending_dns_records` — the
    SPF (TXT) and DKIM (TXT) entries the customer must add at their DNS
    provider. Mailgun does not manage DMARC; that TXT record is the
    customer's own to add (recommended in the settings page copy).
    """
    return _request("POST", "domains", {"name": domain})


def get_domain_status(domain: str) -> dict:
    """Fetch the domain's current state and DNS record verification status."""
    return _request("GET", f"domains/{domain}")


def trigger_verification(domain: str) -> dict:
    """Ask Mailgun to re-check DNS right now instead of waiting for its poll."""
    return _request("PUT", f"domains/{domain}/verify")


def extract_sending_dns_records(domain_record: dict) -> list[dict]:
    """Pull the {record_type, name, value, valid} rows a customer needs to
    see out of either add_domain's or get_domain_status's response shape."""
    domain = domain_record.get("domain", domain_record)
    records = domain.get("sending_dns_records") or domain_record.get("sending_dns_records") or []
    return [
        {
            "record_type": r.get("record_type"),
            "name": r.get("name"),
            "value": r.get("value"),
            "valid": r.get("valid"),
        }
        for r in records
    ]


def is_domain_fully_verified(domain_record: dict) -> bool:
    records = extract_sending_dns_records(domain_record)
    if not records:
        return False
    return all(r.get("valid") == "valid" for r in records)
