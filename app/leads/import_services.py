"""Customer's own lead import: a CSV/Excel export of contacts they already have.

Not the listing catalogue import (app/listings/import_services.py, superadmin):
this one creates leads directly in the customer's organization.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.leads.models import Lead
from app.leads.services import LeadService, LeadServiceError

MAX_IMPORT_ROWS = 2000

# Canonical lead field -> accepted header spellings (lowercase, accent-free).
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "company": ("yritys", "taloyhtio", "yhtio", "company", "organisaatio", "asiakas"),
    "first_name": ("etunimi", "first_name", "firstname"),
    "last_name": ("sukunimi", "last_name", "lastname"),
    "full_name": ("nimi", "yhteyshenkilo", "name", "contact"),
    "email": ("sahkoposti", "email", "e_mail", "sposti"),
    "phone": ("puhelin", "puh", "phone", "puhelinnumero", "gsm"),
    "title": ("titteli", "tehtava", "title", "asema", "ammatti"),
    "website": ("verkkosivu", "kotisivu", "website", "www", "url"),
    "deal_value": ("kaupan_arvo", "arvo", "deal_value", "summa"),
    "tags": ("tunnisteet", "tagit", "tags"),
    "notes": ("muistiinpanot", "lisatiedot", "notes", "huomiot", "kuvaus"),
    "city": ("kaupunki", "paikkakunta", "city"),
}
_HEADER_TO_FIELD = {alias: field for field, aliases in _COLUMN_ALIASES.items() for alias in aliases}

CSV_TEMPLATE_COLUMNS = (
    "yritys",
    "etunimi",
    "sukunimi",
    "sahkoposti",
    "puhelin",
    "titteli",
    "verkkosivu",
    "kaupunki",
    "kaupan_arvo",
    "tunnisteet",
    "muistiinpanot",
)


class LeadImportError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _header_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "_".join(text.strip().lower().replace("-", " ").split())


def csv_template() -> str:
    """Template with one example row; BOM + semicolons so Excel opens it right."""
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(CSV_TEMPLATE_COLUMNS)
    writer.writerow(
        [
            "As Oy Esimerkkitalo",
            "Matti",
            "Meikäläinen",
            "matti@esimerkki.fi",
            "040 123 4567",
            "Hallituksen puheenjohtaja",
            "https://esimerkki.fi",
            "Helsinki",
            "45000",
            "putkiremontti, messut",
            "Tavattu messuilla, pyysi tarjousta keväälle.",
        ]
    )
    return buffer.getvalue()


def parse_lead_csv(data: bytes) -> list[dict]:
    """Rows as dicts keyed by lead field. Accepts comma, semicolon or tab
    separated files in UTF-8 or Windows-1252 (Excel)."""
    if not data or not data.strip():
        raise LeadImportError("Tiedosto on tyhjä.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")

    header_line = text.lstrip().splitlines()[0]
    delimiter = max((";", ",", "\t"), key=header_line.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(row for row in reader if any(cell.strip() for cell in row))
    except StopIteration:
        raise LeadImportError("Tiedosto on tyhjä.") from None

    columns = [_HEADER_TO_FIELD.get(_header_key(cell)) for cell in header]
    if not any(columns):
        raise LeadImportError(
            "Tiedostosta ei löytynyt tunnistettavia sarakkeita. Lataa mallitiedosto ja käytä sen otsikoita."
        )
    if not ({"company", "full_name", "first_name", "email", "phone"} & set(columns)):
        raise LeadImportError(
            "Tiedostossa pitää olla vähintään yksi sarakkeista: yritys, nimi, sahkoposti tai puhelin."
        )

    rows: list[dict] = []
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue
        row: dict = {}
        for column, cell in zip(columns, cells):
            if column and cell.strip() and column not in row:
                row[column] = cell.strip()
        rows.append(row)
        if len(rows) > MAX_IMPORT_ROWS:
            raise LeadImportError(f"Tiedostossa on yli {MAX_IMPORT_ROWS} riviä. Jaa se pienempiin osiin.")
    if not rows:
        raise LeadImportError("Tiedostossa ei ole yhtään riviä otsikon lisäksi.")
    return rows


def _existing_keys(organization_id: int) -> set[tuple[str, str]]:
    """(email, company) of the organization's open leads, lowercased."""
    rows = (
        db.session.query(Lead.email, Lead.company)
        .filter(Lead.organization_id == organization_id, Lead.status != "archived")
        .all()
    )
    return {((email or "").strip().lower(), (company or "").strip().lower()) for email, company in rows}


def _lead_data(row: dict, assigned_to: int | None) -> dict:
    first_name, last_name = row.get("first_name"), row.get("last_name")
    if row.get("full_name") and not (first_name or last_name):
        parts = row["full_name"].split(None, 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else None

    data: dict = {
        "company": (row.get("company") or "")[:255] or None,
        "first_name": (first_name or "")[:100] or None,
        "last_name": (last_name or "")[:100] or None,
        "email": (row.get("email") or "").strip().lower() or None,
        "phone": (row.get("phone") or "")[:50] or None,
        "title": (row.get("title") or "")[:150] or None,
        "notes": row.get("notes") or None,
        "source": "import",
        "assigned_to": assigned_to,
    }
    website = (row.get("website") or "").strip()
    if website:
        data["website"] = website if website.lower().startswith(("http://", "https://")) else f"https://{website}"
    if row.get("tags"):
        data["tags"] = [t.strip()[:50] for t in row["tags"].replace(";", ",").split(",") if t.strip()]
    if row.get("deal_value"):
        raw = row["deal_value"].replace("€", "").replace(" ", "").replace("\xa0", "").replace(",", ".")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise LeadServiceError(f"Virheellinen kaupan arvo: {row['deal_value']}", "validation_error") from None
        if value < 0:
            raise LeadServiceError("Kaupan arvo ei voi olla negatiivinen.", "validation_error")
        data["deal_value"] = value
    if row.get("city"):
        data["ai_contact_info"] = {"kaupunki": row["city"][:100]}
    return data


def import_leads(
    rows: list[dict],
    organization_id: int,
    user_id: int | None,
    *,
    actor_role: str | None = None,
    assigned_to: int | None = None,
) -> dict:
    """Create a lead per row. A row that matches an existing open lead (same
    email and company) or an earlier row of the file is skipped, a row the
    lead rules reject is reported with its line number; the rest are saved.
    Returns {"created", "skipped", "errors": [..]}."""
    from app.core.messages_fi import fi_message

    result = {"created": 0, "skipped": 0, "errors": []}
    seen = _existing_keys(organization_id)
    for index, row in enumerate(rows, start=2):  # line 1 is the header
        key = ((row.get("email") or "").strip().lower(), (row.get("company") or "").strip().lower())
        if key != ("", "") and key in seen:
            result["skipped"] += 1
            continue
        try:
            with db.session.begin_nested():
                LeadService.create(
                    _lead_data(row, assigned_to), organization_id, user_id, actor_role=actor_role
                )
        except LeadServiceError as exc:
            if len(result["errors"]) < 20:
                result["errors"].append(f"rivi {index}: {fi_message(exc.message)}")
            else:
                result.setdefault("more_errors", 0)
                result["more_errors"] += 1
            continue
        seen.add(key)
        result["created"] += 1
    return result
