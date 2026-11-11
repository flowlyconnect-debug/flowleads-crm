"""Source-independent listing import (CSV upload or API).

The Oikotie crawler is one way to fill the catalogue; this is the other. A row
describes one housing company's upcoming renovation(s) and comes from any
source the operator is entitled to use (own research, a licensed data feed such
as the housing-information system, a partner's export).

Two kinds of rows:
    * with `remonttityyppi` -> stored as analysed, matched and turned into leads
      right away (no AI, no n8n);
    * without it but with `teksti` -> queued for the n8n Analyzer, which
      classifies the text exactly like an Oikotie listing page.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from datetime import datetime

from app.extensions import db
from app.listings.models import Listing
from app.listings.services import (
    IMPORT_SOURCE,
    REGION_SLUGS,
    SOURCE,
    ListingServiceError,
    _clean,
    _content_hash,
    _now,
    _parse_building_type,
    _parse_datetime,
    _parse_year,
    save_analysis,
)
from app.search.constants import REMONTTITYYPIT

MAX_IMPORT_ROWS = 2000
MAX_TEXT_LENGTH = 20000
MIN_TEXT_LENGTH = 30

# Canonical column -> accepted header spellings (after _header_key()).
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "external_id": ("external_id", "tunniste", "id", "y_tunnus", "ytunnus"),
    "taloyhtio": ("taloyhtio", "yhtio", "asunto_osakeyhtio", "nimi"),
    "osoite": ("osoite", "katuosoite"),
    "kaupunki": ("kaupunki", "paikkakunta", "kunta"),
    "maakunta": ("maakunta",),
    "description": ("teksti", "kuvaus", "description", "lisatiedot"),
    "remonttityyppi": ("remonttityyppi", "remonttityypit", "remontti", "remontit"),
    "timing": ("ajankohta", "timing", "vuosi", "remontin_ajankohta"),
    "confidence": ("varmuus", "confidence"),
    "tulevat_remontit": ("tulevat_remontit", "yhteenveto"),
    "isannoitsija": ("isannoitsija", "isannointi", "isannoitsijatoimisto"),
    "isannoitsija_puh": ("isannoitsija_puh", "puhelin", "puh"),
    "isannoitsija_email": ("isannoitsija_email", "sahkoposti", "email"),
    "build_year": ("rakennusvuosi", "build_year"),
    "building_type": ("talotyyppi", "building_type"),
    "url": ("url", "linkki", "lahde"),
    "published_at": ("julkaistu", "published_at", "paivamaara"),
}
_HEADER_TO_COLUMN = {alias: column for column, aliases in _COLUMN_ALIASES.items() for alias in aliases}

CSV_TEMPLATE_COLUMNS = (
    "taloyhtio",
    "osoite",
    "kaupunki",
    "maakunta",
    "remonttityyppi",
    "ajankohta",
    "teksti",
    "isannoitsija",
    "isannoitsija_puh",
    "isannoitsija_email",
    "rakennusvuosi",
    "talotyyppi",
    "url",
    "tunniste",
)

_REGIONS_BY_KEY: dict[str, str] = {}
_TYPES_BY_KEY: dict[str, str] = {}


def _fold(value) -> str:
    """Lowercase, accent-free, single-spaced: 'Päijät-Häme ' -> 'paijat-hame'."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def _header_key(value) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _fold(value)).strip("_")


def _region(value) -> str | None:
    if not _REGIONS_BY_KEY:
        _REGIONS_BY_KEY.update({_fold(name): name for name in REGION_SLUGS})
    return _REGIONS_BY_KEY.get(_fold(value))


def _renovation_type(value) -> str | None:
    if not _TYPES_BY_KEY:
        for name in REMONTTITYYPIT:
            _TYPES_BY_KEY[_fold(name)] = name
            # "Lukkoremontti / kulunvalvonta" is also accepted as "Lukkoremontti"
            _TYPES_BY_KEY.setdefault(_fold(name.split("/")[0]), name)
    return _TYPES_BY_KEY.get(_fold(value))


def _confidence(value) -> int:
    try:
        return max(0, min(100, int(float(str(value).replace("%", "").replace(",", ".").strip()))))
    except (TypeError, ValueError):
        return 100


def parse_listing_csv(data: bytes) -> list[dict]:
    """Rows of a CSV export as dicts keyed by canonical column name. Accepts
    comma, semicolon or tab separated files in UTF-8 or Windows-1252 (Excel)."""
    if not data or not data.strip():
        raise ListingServiceError("Tiedosto on tyhjä.")
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
        raise ListingServiceError("Tiedosto on tyhjä.") from None

    columns = [_HEADER_TO_COLUMN.get(_header_key(cell)) for cell in header]
    if "maakunta" not in columns:
        raise ListingServiceError("Tiedostosta puuttuu sarake 'maakunta'.")
    if "taloyhtio" not in columns and "osoite" not in columns:
        raise ListingServiceError("Tiedostosta puuttuu sarake 'taloyhtio' tai 'osoite'.")

    rows: list[dict] = []
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue
        row: dict = {}
        for column, cell in zip(columns, cells):
            # first matching column wins when two headers map to the same field
            if column and cell.strip() and column not in row:
                row[column] = cell.strip()
        rows.append(row)
        if len(rows) > MAX_IMPORT_ROWS:
            raise ListingServiceError(f"Tiedostossa on yli {MAX_IMPORT_ROWS} riviä. Jaa se pienempiin osiin.")
    if not rows:
        raise ListingServiceError("Tiedostossa ei ole yhtään riviä otsikon lisäksi.")
    return rows


def _renovations(raw: dict) -> tuple[list[dict], str | None]:
    """(renovations, error). Accepts a ready `renovations` list (API) or a
    `remonttityyppi` cell with one or more types separated by ; | or a comma
    (no renovation type name contains one)."""
    items = raw.get("renovations")
    if isinstance(items, list) and items:
        parsed = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _renovation_type(item.get("remonttityyppi"))
            if not name:
                return [], f"Tuntematon remonttityyppi: {item.get('remonttityyppi')}"
            parsed.append(
                {
                    "remonttityyppi": name,
                    "confidence": _confidence(item.get("confidence", 100)),
                    "timing": _clean(item.get("timing"), 100) or "",
                    "evidence": _clean(item.get("evidence"), 2000) or "",
                }
            )
        return parsed, None

    cell = _clean(raw.get("remonttityyppi"))
    if not cell:
        return [], None
    parsed = []
    for part in re.split(r"[;|,]", cell):
        if not part.strip():
            continue
        name = _renovation_type(part)
        if not name:
            return [], f"Tuntematon remonttityyppi: {part.strip()}"
        parsed.append(
            {
                "remonttityyppi": name,
                "confidence": _confidence(raw.get("confidence", 100)),
                "timing": _clean(raw.get("timing"), 100) or "",
                "evidence": (_clean(raw.get("description"), 2000) or ""),
            }
        )
    return parsed, None


def _external_id(raw: dict, taloyhtio: str | None, osoite: str | None, kaupunki: str | None) -> str:
    given = _clean(raw.get("external_id"), 50)
    if given:
        return given
    key = "|".join(_fold(part) for part in (taloyhtio, osoite, kaupunki))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def import_listings(rows: list, *, source: str = IMPORT_SOURCE, now: datetime | None = None) -> dict:
    """Upsert listings from a source other than the Oikotie crawler. Rows with
    renovation types become leads for matching profiles immediately; rows with
    only text wait for the n8n Analyzer. Bad rows are reported, not fatal."""
    if not isinstance(rows, list):
        raise ListingServiceError("listings must be a list")
    if len(rows) > MAX_IMPORT_ROWS:
        raise ListingServiceError(f"At most {MAX_IMPORT_ROWS} listings per import.")
    source = (_clean(source, 30) or IMPORT_SOURCE).lower()
    if source == SOURCE:
        raise ListingServiceError("Oikotie listings come from the crawler (/ingest), not from import.")

    now = now or _now()
    result = {"created": 0, "updated": 0, "analysed": 0, "queued": 0, "matches": 0, "leads_created": 0, "errors": []}

    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            result["errors"].append({"row": index, "error": "Rivi ei ole kelvollinen."})
            continue
        maakunta = _region(raw.get("maakunta"))
        taloyhtio = _clean(raw.get("taloyhtio"), 255)
        osoite = _clean(raw.get("osoite"), 255)
        kaupunki = _clean(raw.get("kaupunki"), 100)
        description = _clean(raw.get("description"), MAX_TEXT_LENGTH)
        if not maakunta:
            result["errors"].append({"row": index, "error": f"Tuntematon maakunta: {raw.get('maakunta') or '(tyhjä)'}"})
            continue
        if not taloyhtio and not osoite:
            result["errors"].append({"row": index, "error": "Taloyhtiö tai osoite puuttuu."})
            continue
        renovations, error = _renovations(raw)
        if error:
            result["errors"].append({"row": index, "error": error})
            continue
        if not renovations and len(description or "") < MIN_TEXT_LENGTH:
            result["errors"].append(
                {"row": index, "error": "Rivillä pitää olla remonttityyppi tai vähintään 30 merkin teksti."}
            )
            continue

        external_id = _external_id(raw, taloyhtio, osoite, kaupunki)
        listing = Listing.query.filter_by(source=source, external_id=external_id).first()
        digest = _content_hash(description)
        is_new = listing is None
        if is_new:
            listing = Listing(
                source=source,
                external_id=external_id,
                maakunta=maakunta,
                first_seen_at=now,
                analysis_status="pending",
                content_hash=digest,
            )
            db.session.add(listing)
            result["created"] += 1
        else:
            result["updated"] += 1
            if listing.content_hash != digest and listing.analysis_status in ("done", "failed"):
                listing.analysis_status = "pending"
                listing.analysis_attempts = 0
            listing.content_hash = digest

        listing.maakunta = maakunta
        listing.last_seen_at = now
        listing.is_active = True
        listing.removed_at = None
        for field, value in (
            ("taloyhtio", taloyhtio),
            ("osoite", osoite),
            ("kaupunki", kaupunki),
            ("description", description),
            ("url", _clean(raw.get("url"), 500)),
            ("published_at", _parse_datetime(raw.get("published_at"))),
            ("build_year", _parse_year(raw.get("build_year"))),
            ("building_type", _parse_building_type(raw.get("building_type"))),
        ):
            if value is not None:
                setattr(listing, field, value)
        db.session.commit()

        if not renovations:
            # contacts given on the row are kept; the Analyzer adds the rest
            for field, max_len in (("isannoitsija", 255), ("isannoitsija_puh", 50), ("isannoitsija_email", 255)):
                value = _clean(raw.get(field), max_len)
                if value:
                    setattr(listing, field, value)
            if listing.has_contact and not listing.contact_source:
                listing.contact_source = source
            db.session.commit()
            if listing.analysis_status == "pending":
                result["queued"] += 1
            continue

        summary = _clean(raw.get("tulevat_remontit"), 5000) or ", ".join(
            " ".join(p for p in (r["remonttityyppi"], r["timing"]) if p) for r in renovations
        )
        saved = save_analysis(
            listing.id,
            {
                "status": "done",
                "taloyhtio": taloyhtio,
                "tulevat_remontit": summary,
                "isannoitsija": raw.get("isannoitsija"),
                "isannoitsija_puh": raw.get("isannoitsija_puh"),
                "isannoitsija_email": raw.get("isannoitsija_email"),
                "renovations": renovations,
            },
            now=now,
        )
        result["analysed"] += 1
        result["matches"] += saved.get("matches", 0)
        result["leads_created"] += saved.get("leads_created", 0)

    return result


def csv_template() -> str:
    """A small example file an operator can fill in (semicolon separated, the
    format Finnish Excel writes)."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_TEMPLATE_COLUMNS)
    writer.writerow(
        [
            "As Oy Esimerkkikatu 1",
            "Esimerkkikatu 1",
            "Tampere",
            "Pirkanmaa",
            "Putkiremontti",
            "2027",
            "Yhtiökokous on päättänyt linjasaneerauksen hankesuunnittelusta. Toteutus arviolta 2027.",
            "Isännöinti Esimerkki Oy",
            "040 123 4567",
            "isannointi@esimerkki.fi",
            "1974",
            "Kerrostalo",
            "",
            "",
        ]
    )
    writer.writerow(
        [
            "As Oy Mallitie 5",
            "Mallitie 5",
            "Espoo",
            "Uusimaa",
            "",
            "",
            "Taloyhtiössä on suunnitteilla lukituksen uusiminen ja kulunvalvonta vuonna 2026. "
            "Julkisivuremontti tehty 2019.",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    return out.getvalue()
