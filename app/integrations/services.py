"""Export leads to HubSpot or Pipedrive: as an import file (no connection
needed) or straight through the CRM's API with the organization's token.

A FlowLeads lead is one housing company's renovation: it becomes a company
(the taloyhtiö), a contact (the property manager or the board chair) and a deal
(the renovation) in the target CRM. Exports are idempotent: CrmExportLink
remembers the created objects and a second export updates them.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

import requests

from app.extensions import db
from app.integrations.models import PROVIDER_LABELS, PROVIDERS, CrmConnection, CrmExportLink

logger = logging.getLogger(__name__)

HUBSPOT_API = "https://api.hubapi.com"
PIPEDRIVE_API = "https://api.pipedrive.com/v1"
TIMEOUT = 15
MAX_PUSH = 50


class CrmExportError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- what a lead looks like outside FlowLeads ---------------------------------------


def _meta(lead) -> dict:
    return lead.ai_contact_info if isinstance(lead.ai_contact_info, dict) else {}


def _split_name(name: str) -> tuple[str, str]:
    parts = (name or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def lead_record(lead) -> dict:
    """The lead in CRM-neutral terms."""
    from app import stage_label_fi

    meta = _meta(lead)
    company = lead.company or meta.get("taloyhtio") or lead.display_name
    person = (
        meta.get("isannoitsija_henkilo")
        or " ".join(p for p in (lead.first_name, lead.last_name) if p)
        or meta.get("hallitus_pj")
        or ""
    )
    first, last = (lead.first_name or "", lead.last_name or "") if (lead.first_name or lead.last_name) else _split_name(person)
    renovation = meta.get("remonttityyppi") or ""
    timing = meta.get("remontin_ajankohta") or ""
    address = ", ".join(p for p in (meta.get("osoite"), meta.get("kaupunki")) if p)
    deal = " – ".join(p for p in (company, renovation) if p) or company
    summary = "\n".join(
        p
        for p in (
            renovation and f"Remontti: {renovation}" + (f" ({timing})" if timing else ""),
            meta.get("tulevat_remontit") and f"Tulevat remontit: {meta.get('tulevat_remontit')}",
            meta.get("isannoitsija_yritys") and f"Isännöinti: {meta.get('isannoitsija_yritys')}",
            meta.get("hallitus_pj") and f"Hallituksen puheenjohtaja: {meta.get('hallitus_pj')} {meta.get('hallitus_pj_puh') or ''}".strip(),
            meta.get("huoltoyhtio") and f"Huoltoyhtiö: {meta.get('huoltoyhtio')} {meta.get('huoltoyhtio_puh') or ''}".strip(),
            meta.get("rakennusvuosi") and f"Rakennusvuosi: {meta.get('rakennusvuosi')}",
            "Lähde: FlowLeads",
        )
        if p
    )
    return {
        "company": (company or "")[:255],
        "company_ytunnus": meta.get("taloyhtio_ytunnus") or "",
        "address": meta.get("osoite") or "",
        "city": meta.get("kaupunki") or "",
        "full_address": address,
        "region": meta.get("maakunta") or lead.region or "",
        "manager_office": meta.get("isannoitsija_yritys") or "",
        "first_name": first[:100],
        "last_name": last[:100],
        "contact_name": " ".join(p for p in (first, last) if p) or person,
        "email": lead.email or "",
        "phone": lead.phone or meta.get("isannoitsija_puh") or "",
        "job_title": "Isännöitsijä" if meta.get("isannoitsija_yritys") else (lead.title or ""),
        "deal_name": deal[:255],
        "amount": float(lead.deal_value) if lead.deal_value is not None else None,
        "stage": stage_label_fi(lead.stage.name) if lead.stage else "",
        "renovation": renovation,
        "timing": timing,
        "summary": summary,
        "build_year": meta.get("rakennusvuosi") or "",
    }


# --- import files ---------------------------------------------------------------

HUBSPOT_COLUMNS = (
    ("Company name", "company"),
    ("Company street address", "address"),
    ("Company city", "city"),
    ("First Name", "first_name"),
    ("Last Name", "last_name"),
    ("Email", "email"),
    ("Phone Number", "phone"),
    ("Job Title", "job_title"),
    ("Deal Name", "deal_name"),
    ("Amount", "amount"),
    ("Deal Description", "summary"),
)
PIPEDRIVE_COLUMNS = (
    ("Organization - Name", "company"),
    ("Organization - Address", "full_address"),
    ("Person - Name", "contact_name"),
    ("Person - Email", "email"),
    ("Person - Phone", "phone"),
    ("Deal - Title", "deal_name"),
    ("Deal - Value", "amount"),
    ("Deal - Currency", None),
    ("Note - Content", "summary"),
)


def _csv_cell(value) -> str:
    from app.leads.validators import sanitize_csv_value

    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return sanitize_csv_value(str(value))


def import_file(provider: str, leads) -> str:
    """A CSV the CRM's own import tool reads (comma separated, UTF-8, column
    names the CRM maps automatically)."""
    columns = {"hubspot": HUBSPOT_COLUMNS, "pipedrive": PIPEDRIVE_COLUMNS}.get(provider)
    if columns is None:
        raise CrmExportError("Unknown CRM.")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([label for label, _ in columns])
    for lead in leads:
        record = lead_record(lead)
        writer.writerow(["EUR" if key is None else _csv_cell(record.get(key)) for _, key in columns])
    return out.getvalue()


# --- connections ---------------------------------------------------------------------


def get_connection(organization_id: int, provider: str) -> CrmConnection | None:
    return CrmConnection.query.filter_by(organization_id=organization_id, provider=provider).first()


def connections(organization_id: int) -> dict:
    return {c.provider: c for c in CrmConnection.query.filter_by(organization_id=organization_id).all()}


def _token(connection: CrmConnection) -> str:
    from app.webhooks.encryption import decrypt_webhook_secret

    return decrypt_webhook_secret(connection.token_encrypted)


def connect(organization_id: int, provider: str, token: str, *, user_id: int | None, settings: dict | None = None, session=None):
    """Check the token against the CRM and store it. Raises CrmExportError."""
    from app.webhooks.encryption import encrypt_webhook_secret

    if provider not in PROVIDERS:
        raise CrmExportError("Unknown CRM.")
    token = (token or "").strip()
    if len(token) < 10 or any(ch.isspace() for ch in token):
        raise CrmExportError("The API token looks incomplete.")
    client = _client(provider, token, settings or {}, session=session)
    label = client.check()
    connection = get_connection(organization_id, provider)
    if connection is None:
        connection = CrmConnection(organization_id=organization_id, provider=provider, settings={})
        db.session.add(connection)
    connection.token_encrypted = encrypt_webhook_secret(token)
    connection.settings = {k: v for k, v in (settings or {}).items() if v}
    connection.account_label = (label or "")[:255] or None
    connection.connected_by = user_id
    connection.connected_at = _now()
    connection.last_error = None
    db.session.flush()
    return connection


def disconnect(organization_id: int, provider: str) -> bool:
    connection = get_connection(organization_id, provider)
    if connection is None:
        return False
    db.session.delete(connection)
    db.session.flush()
    return True


# --- API clients ---------------------------------------------------------------------


class _Client:
    def __init__(self, token: str, settings: dict, session=None):
        self.token = token
        self.settings = settings
        self.http = session or requests.Session()

    def _request(self, method: str, url: str, **kwargs):
        try:
            response = self.http.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise CrmExportError(f"{self.label} did not answer: {exc.__class__.__name__}") from exc
        if response.status_code in (401, 403):
            raise CrmExportError(f"{self.label} rejected the API token.")
        if response.status_code == 429:
            raise CrmExportError(f"{self.label} rate limit reached. Try again in a minute.")
        if response.status_code >= 400:
            raise CrmExportError(f"{self.label} refused the data: HTTP {response.status_code} {response.text[:200]}")
        try:
            return response.json() if response.content else {}
        except ValueError:
            return {}


class HubSpotClient(_Client):
    label = "HubSpot"

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def check(self) -> str:
        self._request("GET", f"{HUBSPOT_API}/crm/v3/objects/companies?limit=1", headers=self._headers())
        return "HubSpot"

    def _upsert(self, kind: str, object_id: str | None, properties: dict) -> str:
        properties = {k: v for k, v in properties.items() if v not in (None, "")}
        if object_id:
            self._request("PATCH", f"{HUBSPOT_API}/crm/v3/objects/{kind}/{object_id}", headers=self._headers(), json={"properties": properties})
            return object_id
        data = self._request("POST", f"{HUBSPOT_API}/crm/v3/objects/{kind}", headers=self._headers(), json={"properties": properties})
        if not data.get("id"):
            raise CrmExportError("HubSpot did not return an id.")
        return str(data["id"])

    def _associate(self, from_kind: str, from_id: str, to_kind: str, to_id: str) -> None:
        self._request(
            "PUT",
            f"{HUBSPOT_API}/crm/v4/objects/{from_kind}/{from_id}/associations/default/{to_kind}/{to_id}",
            headers=self._headers(),
        )

    def push(self, record: dict, link: CrmExportLink) -> None:
        link.company_id = self._upsert(
            "companies",
            link.company_id,
            {"name": record["company"], "address": record["address"], "city": record["city"], "country": "Finland"},
        )
        if record["email"] or record["phone"] or record["contact_name"]:
            link.contact_id = self._upsert(
                "contacts",
                link.contact_id,
                {
                    "firstname": record["first_name"] or record["contact_name"],
                    "lastname": record["last_name"],
                    "email": record["email"],
                    "phone": record["phone"],
                    "jobtitle": record["job_title"],
                    "company": record["company"],
                },
            )
        new_deal = not link.deal_id
        deal = {
            "dealname": record["deal_name"],
            "amount": record["amount"],
            "description": record["summary"][:5000],
        }
        if new_deal:
            # a deal needs a pipeline and a stage; the defaults exist in every portal
            deal["pipeline"] = self.settings.get("pipeline") or "default"
            deal["dealstage"] = self.settings.get("dealstage") or "appointmentscheduled"
        link.deal_id = self._upsert("deals", link.deal_id, deal)
        self._associate("deals", link.deal_id, "companies", link.company_id)
        if link.contact_id:
            self._associate("contacts", link.contact_id, "companies", link.company_id)
            self._associate("deals", link.deal_id, "contacts", link.contact_id)


class PipedriveClient(_Client):
    label = "Pipedrive"

    def _headers(self):
        # the token in a header keeps it out of URLs and proxy logs
        return {"x-api-token": self.token, "Content-Type": "application/json"}

    def check(self) -> str:
        data = self._request("GET", f"{PIPEDRIVE_API}/users/me", headers=self._headers())
        me = data.get("data") or {}
        return me.get("company_name") or me.get("company_domain") or "Pipedrive"

    def _upsert(self, kind: str, object_id: str | None, payload: dict) -> str:
        payload = {k: v for k, v in payload.items() if v not in (None, "", [])}
        if object_id:
            self._request("PUT", f"{PIPEDRIVE_API}/{kind}/{object_id}", headers=self._headers(), json=payload)
            return object_id
        data = self._request("POST", f"{PIPEDRIVE_API}/{kind}", headers=self._headers(), json=payload)
        new_id = (data.get("data") or {}).get("id")
        if not new_id:
            raise CrmExportError("Pipedrive did not return an id.")
        return str(new_id)

    def push(self, record: dict, link: CrmExportLink) -> None:
        link.company_id = self._upsert("organizations", link.company_id, {"name": record["company"], "address": record["full_address"]})
        if record["email"] or record["phone"] or record["contact_name"]:
            link.contact_id = self._upsert(
                "persons",
                link.contact_id,
                {
                    "name": record["contact_name"] or record["company"],
                    "email": [{"value": record["email"], "primary": True}] if record["email"] else [],
                    "phone": [{"value": record["phone"], "primary": True}] if record["phone"] else [],
                    "org_id": int(link.company_id),
                },
            )
        new_deal = not link.deal_id
        link.deal_id = self._upsert(
            "deals",
            link.deal_id,
            {
                "title": record["deal_name"],
                "value": record["amount"],
                "currency": "EUR",
                "org_id": int(link.company_id),
                "person_id": int(link.contact_id) if link.contact_id else None,
            },
        )
        if new_deal and record["summary"]:
            self._upsert("notes", None, {"content": record["summary"].replace("\n", "<br>"), "deal_id": int(link.deal_id)})


def _client(provider: str, token: str, settings: dict, session=None):
    return {"hubspot": HubSpotClient, "pipedrive": PipedriveClient}[provider](token, settings, session=session)


def push_leads(organization_id: int, provider: str, leads, *, session=None) -> dict:
    """Create or update the leads in the connected CRM. One failing lead does
    not stop the others; an authentication failure stops the run."""
    from app.leads.services import LeadService

    connection = get_connection(organization_id, provider)
    if connection is None:
        raise CrmExportError(f"{PROVIDER_LABELS.get(provider, provider)} is not connected.")
    leads = list(leads)[:MAX_PUSH]
    client = _client(provider, _token(connection), connection.settings or {}, session=session)
    result = {"created": 0, "updated": 0, "failed": 0, "errors": []}
    for lead in leads:
        if lead.organization_id != organization_id:
            continue
        link = CrmExportLink.query.filter_by(lead_id=lead.id, provider=provider).first()
        existed = link is not None and bool(link.deal_id)
        if link is None:
            link = CrmExportLink(organization_id=organization_id, lead_id=lead.id, provider=provider)
            db.session.add(link)
        try:
            client.push(lead_record(lead), link)
        except CrmExportError as exc:
            result["failed"] += 1
            result["errors"].append({"lead_id": lead.id, "error": exc.message})
            connection.last_error = exc.message[:500]
            if link.id is None and not link.company_id:
                db.session.expunge(link)
            elif link.company_id or link.deal_id:
                db.session.flush()  # keep the objects that were created
            db.session.commit()
            if "rejected the API token" in exc.message:
                break
            continue
        link.exported_at = _now()
        connection.last_used_at = link.exported_at
        connection.last_error = None
        result["updated" if existed else "created"] += 1
        try:
            LeadService.log_activity(
                lead.id,
                None,
                "updated",
                content=f"Viety {PROVIDER_LABELS[provider]}iin" + (" (päivitetty)" if existed else ""),
            )
        except Exception:  # the history note must not undo the export
            logger.exception("Could not log CRM export of lead %s", lead.id)
        db.session.commit()
    return result


def export_links(lead_ids) -> dict:
    """{lead_id: {provider: CrmExportLink}}"""
    links: dict = {}
    if not lead_ids:
        return links
    for link in CrmExportLink.query.filter(CrmExportLink.lead_id.in_(list(lead_ids))).all():
        links.setdefault(link.lead_id, {})[link.provider] = link
    return links
