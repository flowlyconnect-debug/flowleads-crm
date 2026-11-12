"""People and offices behind a housing company: the property management
office (isännöintitoimisto), the chair of the board and the maintenance
company. Shared catalogue data, like the listings themselves.

* Offices live in `PropertyManagerOffice`, keyed by `office_key(name)`, and are
  filled from listing import rows (isannointitoimisto_* columns), from an office
  list (`import_offices`, CSV on /admin/catalogue or the n8n API) and never
  overwritten with empty values.
* The board chair and the maintenance company are listing columns; a housing
  company with several listings shows the first value found
  (`company_contacts`).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone

from app.extensions import db
from app.listings.housing import fold, normalize_ytunnus

MAX_OFFICE_ROWS = 5000
_LEGAL_SUFFIXES = (" oy", " oyj", " ab", " ky", " tmi", " ay", " ltd")

# office CSV: canonical column -> accepted header spellings (folded, _ separated)
_OFFICE_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("isannointitoimisto", "toimisto", "nimi", "yritys", "isannointiyritys", "name"),
    "ytunnus": ("y_tunnus", "ytunnus", "business_id"),
    "osoite": ("osoite", "katuosoite", "address"),
    "postinumero": ("postinumero", "postinro", "zip"),
    "kaupunki": ("kaupunki", "postitoimipaikka", "paikkakunta", "city"),
    "puhelin": ("puhelin", "vaihde", "puh", "phone"),
    "email": ("sahkoposti", "email", "s_posti"),
    "website": ("www", "verkkosivu", "verkkosivut", "website", "kotisivu"),
}
_OFFICE_HEADERS = {alias: column for column, aliases in _OFFICE_ALIASES.items() for alias in aliases}
OFFICE_TEMPLATE_COLUMNS = ("isannointitoimisto", "y_tunnus", "osoite", "postinumero", "kaupunki", "puhelin", "sahkoposti", "www")

# listing import columns that describe the office of the row's manager
LISTING_OFFICE_FIELDS = {
    "isannointitoimisto_ytunnus": "ytunnus",
    "isannointitoimisto_osoite": "osoite",
    "isannointitoimisto_postinumero": "postinumero",
    "isannointitoimisto_kaupunki": "kaupunki",
    "isannointitoimisto_puh": "puhelin",
    "isannointitoimisto_email": "email",
    "isannointitoimisto_www": "website",
}

_LIMITS = {"name": 255, "ytunnus": 20, "osoite": 255, "postinumero": 10, "kaupunki": 100, "puhelin": 50, "email": 255, "website": 500}


class OfficeImportError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def office_key(name) -> str:
    """'Isännöinti Kallio Oy' and 'ISÄNNÖINTI KALLIO' are the same office."""
    text = fold(name)
    for suffix in _LEGAL_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    return text[:255]


def _clean(value, field: str) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    if field == "ytunnus":
        return normalize_ytunnus(text)
    if field == "email":
        return text if "@" in text and " " not in text else None
    if field == "website":
        if not re.match(r"^https?://", text, re.I):
            text = "https://" + text.lstrip("/")
        return text[: _LIMITS[field]]
    if field == "postinumero":
        digits = re.sub(r"\D", "", text)
        return digits[:5] or None
    return text[: _LIMITS[field]]


def upsert_office(data: dict, *, source: str | None = None, now: datetime | None = None):
    """Create or complete an office. Given values replace stored ones; empty
    values never erase anything. Returns the office or None (no name)."""
    from app.listings.models import PropertyManagerOffice

    name = _clean(data.get("name"), "name")
    key = office_key(name)
    if not key:
        return None
    now = now or datetime.now(timezone.utc)
    office = PropertyManagerOffice.query.filter_by(name_key=key).first()
    if office is None:
        office = PropertyManagerOffice(name_key=key, name=name, created_at=now, updated_at=now, source=source)
        db.session.add(office)
    changed = False
    for field in ("ytunnus", "osoite", "postinumero", "kaupunki", "puhelin", "email", "website"):
        value = _clean(data.get(field), field)
        if value and getattr(office, field) != value:
            setattr(office, field, value)
            changed = True
    if changed:
        office.updated_at = now
        if source:
            office.source = source
    return office


def offices_for(names) -> dict:
    """{office_key: PropertyManagerOffice} for the given manager names."""
    from app.listings.models import PropertyManagerOffice

    keys = {office_key(name) for name in names if (name or "").strip()}
    keys.discard("")
    if not keys:
        return {}
    return {office.name_key: office for office in PropertyManagerOffice.query.filter(PropertyManagerOffice.name_key.in_(keys)).all()}


def office_for(name):
    return offices_for([name]).get(office_key(name))


def parse_office_csv(data: bytes) -> list[dict]:
    if not data or not data.strip():
        raise OfficeImportError("Tiedosto on tyhjä.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    header_line = text.lstrip().splitlines()[0]
    delimiter = max((";", ",", "\t"), key=header_line.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next((row for row in reader if any(cell.strip() for cell in row)), None)
    if header is None:
        raise OfficeImportError("Tiedosto on tyhjä.")
    columns = [_OFFICE_HEADERS.get(re.sub(r"[^a-z0-9]+", "_", fold(cell)).strip("_")) for cell in header]
    if "name" not in columns:
        raise OfficeImportError("Tiedostosta puuttuu sarake 'isannointitoimisto'.")
    rows = []
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue
        row = {}
        for column, cell in zip(columns, cells):
            if column and cell.strip() and column not in row:
                row[column] = cell.strip()
        rows.append(row)
        if len(rows) > MAX_OFFICE_ROWS:
            raise OfficeImportError(f"Tiedostossa on yli {MAX_OFFICE_ROWS} riviä. Jaa se pienempiin osiin.")
    return rows


def import_offices(rows, *, source: str = "import") -> dict:
    """Upsert an office list. Bad rows are reported, not fatal."""
    if not isinstance(rows, list):
        raise OfficeImportError("offices must be a list")
    if len(rows) > MAX_OFFICE_ROWS:
        raise OfficeImportError(f"At most {MAX_OFFICE_ROWS} offices per import.")
    from app.listings.models import PropertyManagerOffice

    result = {"created": 0, "updated": 0, "errors": []}
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            result["errors"].append({"row": index, "error": "Rivi ei ole kelvollinen."})
            continue
        key = office_key(raw.get("name") or raw.get("isannointitoimisto"))
        if not key:
            result["errors"].append({"row": index, "error": "Toimiston nimi puuttuu."})
            continue
        exists = PropertyManagerOffice.query.filter_by(name_key=key).first() is not None
        upsert_office({"name": raw.get("name") or raw.get("isannointitoimisto"), **raw}, source=source)
        db.session.flush()
        result["updated" if exists else "created"] += 1
    db.session.commit()
    return result


def office_template() -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(OFFICE_TEMPLATE_COLUMNS)
    writer.writerow(
        ["Isännöinti Esimerkki Oy", "1234567-8", "Esimerkkikatu 1", "00100", "Helsinki", "09 123 4567", "info@esimerkki.fi", "www.esimerkki.fi"]
    )
    return out.getvalue()


def office_dict(office) -> dict:
    if office is None:
        return {}
    return {
        "name": office.name,
        "ytunnus": office.ytunnus or "",
        "osoite": ", ".join(p for p in (office.osoite, " ".join(p for p in (office.postinumero, office.kaupunki) if p)) if p),
        "puhelin": office.puhelin or "",
        "email": office.email or "",
        "website": office.website or "",
    }


def company_contacts(listings) -> dict:
    """The board chair and the maintenance company of a housing company: the
    first listing that names them, with the contact details of that listing."""

    def first(name_field: str, phone_field: str, email_field: str) -> dict:
        for listing in listings:
            name = (getattr(listing, name_field, None) or "").strip()
            if name:
                return {
                    "name": name,
                    "phone": (getattr(listing, phone_field, None) or "").strip(),
                    "email": (getattr(listing, email_field, None) or "").strip(),
                }
        for listing in listings:  # contact details without a name
            phone = (getattr(listing, phone_field, None) or "").strip()
            email = (getattr(listing, email_field, None) or "").strip()
            if phone or email:
                return {"name": "", "phone": phone, "email": email}
        return {}

    return {
        "chair": first("hallitus_pj", "hallitus_pj_puh", "hallitus_pj_email"),
        "maintenance": first("huoltoyhtio", "huoltoyhtio_puh", "huoltoyhtio_email"),
    }
