"""Copy the open registers into registry_* tables (internal, see __init__).

Every step is incremental and bounded so the nightly job can run on the small
production database and pick up where it stopped:

- `sync_buildings`: Ryhti residential buildings, delta on aikaleima_utc
- `sync_permits`: Ryhti alteration permits decided in the last 24 months
- `fetch_addresses`: Ryhti addresses for buildings that have none yet
- `sync_prh`: PRH asunto-osakeyhtiöt, a few municipalities per run
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from flask import current_app

from app.extensions import db
from app.listings.housing import fold, name_key
from app.listings.identity import address_key
from app.listings.identity import street_key as _street_key
from app.registry.clients import PrhClient, RegistryFetchError, RyhtiClient
from app.registry.models import (
    RegistryBuilding,
    RegistryHousingCompany,
    RegistryPermit,
    RegistrySyncState,
)

logger = logging.getLogger(__name__)

PERMIT_MONTHS = 24
ADDRESS_BATCH = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cfg(name: str, default):
    return current_app.config.get(name, default)


def _date(value) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _datetime(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _text(value, max_len: int) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:max_len] or None


def state(name: str) -> RegistrySyncState:
    row = RegistrySyncState.query.filter_by(name=name).first()
    if row is None:
        row = RegistrySyncState(name=name, last_rows=0)
        db.session.add(row)
        db.session.flush()
    return row


def _finish(row: RegistrySyncState, rows: int, error: str | None, now: datetime) -> None:
    row.last_run_at = now
    row.last_rows = rows
    row.last_error = error
    if error is None:
        row.last_success_at = now


def municipalities_scope() -> list[str]:
    """REGISTRY_MUNICIPALITIES (comma-separated kunta names); empty = all."""
    raw = _cfg("REGISTRY_MUNICIPALITIES", "") or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


# --- Ryhti buildings ---------------------------------------------------------


def _building_type(props: dict) -> str:
    return "Kerrostalo" if props.get("paaasiallinen_kayttotarkoitus") == "Kerrostalo" else "Rivitalo"


def _apply_building(building: RegistryBuilding, props: dict, now: datetime) -> None:
    completed = _date(props.get("valmistumispaivamaara"))
    modified = _datetime(props.get("aikaleima_utc"))
    previous = building.ryhti_modified_at
    if previous is not None and previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    if modified and previous and modified > previous:
        building.addresses_fetched_at = None  # the building changed: refresh its addresses
    building.building_id = _text(props.get("pysyva_rakennustunnus"), 20)
    building.property_id = _text(props.get("sijaintikiinteisto"), 20)
    building.municipality = _text(props.get("kunta"), 100)
    building.building_type = _building_type(props)
    building.completion_year = completed.year if completed else None
    building.apartments = _int(props.get("huoneistojen_lukumaara"))
    building.floors = _int(props.get("kerrosluku"))
    building.floor_area = _int(props.get("kerrosala"))
    building.facade_material = _text(props.get("julkisivumateriaali"), 50)
    building.construction_method = _text(props.get("rakentamistapa"), 50)
    building.heating_method = _text(props.get("lammitystapa"), 50)
    building.heating_source = _text(props.get("lammitysenergian_lahde"), 50)
    building.lat = props.get("_lat")
    building.lon = props.get("_lon")
    building.ryhti_modified_at = modified
    building.synced_at = now


def _upsert_buildings(batch: list[dict], now: datetime) -> int:
    keys = [p.get("rakennusavain") for p in batch if p.get("rakennusavain")]
    existing = {b.building_key: b for b in RegistryBuilding.query.filter(RegistryBuilding.building_key.in_(keys))}
    for props in batch:
        key = props.get("rakennusavain")
        if not key:
            continue
        building = existing.get(key)
        if building is None:
            building = RegistryBuilding(building_key=key, building_type=_building_type(props))
            db.session.add(building)
            existing[key] = building
        _apply_building(building, props, now)
    db.session.flush()
    return len(keys)


def sync_buildings(client: RyhtiClient | None = None, *, max_pages: int | None = None, now: datetime | None = None) -> int:
    """New and changed residential buildings since the last run."""
    client = client or RyhtiClient()
    now = now or _now()
    total = 0
    for municipality in municipalities_scope() or [None]:
        row = state(f"ryhti_buildings:{municipality}" if municipality else "ryhti_buildings")
        rows = 0
        cursor = row.cursor
        error = None
        batch: list[dict] = []
        try:
            for props in client.buildings(since=row.cursor, municipality=municipality, max_pages=max_pages):
                batch.append(props)
                stamp = props.get("aikaleima_utc")
                if stamp and (cursor is None or stamp > cursor):
                    cursor = stamp
                if len(batch) >= 500:
                    rows += _upsert_buildings(batch, now)
                    batch = []
            rows += _upsert_buildings(batch, now)
        except RegistryFetchError as exc:
            error = str(exc)[:1000]
            logger.warning("Ryhti building sync stopped: %s", error)
        # a failed run keeps what it stored; the cursor moves only on success
        if error is None:
            row.cursor = cursor
        _finish(row, rows, error, now)
        db.session.commit()
        total += rows
    return total


# --- Ryhti permits -----------------------------------------------------------


def _upsert_permits(batch: list[dict], now: datetime) -> int:
    pairs = [(p.get("pysyva_lupatunnus"), p.get("rakennusavain")) for p in batch]
    pairs = [(pid, key) for pid, key in pairs if pid and key]
    if not pairs:
        return 0
    existing = {
        (p.permit_id, p.building_key): p
        for p in RegistryPermit.query.filter(RegistryPermit.permit_id.in_({pid for pid, _ in pairs}))
    }
    for props in batch:
        pair = (props.get("pysyva_lupatunnus"), props.get("rakennusavain"))
        if not all(pair):
            continue
        permit = existing.get(pair)
        if permit is None:
            permit = RegistryPermit(permit_id=pair[0], building_key=pair[1])
            db.session.add(permit)
            existing[pair] = permit
        permit.building_id = _text(props.get("pysyva_rakennustunnus"), 20)
        permit.municipality = _text(props.get("lupapaatoksen_kunta"), 100)
        permit.building_type = _building_type(props)
        permit.action_type = _text(props.get("toimenpiteen_laji"), 80)
        permit.action_state = _text(props.get("toimenpiteen_tila"), 30)
        permit.decision_date = _date(props.get("paatospaivamaara"))
        permit.start_date = _date(props.get("aloittamispaivamaara"))
        permit.completion_date = _date(props.get("valmistumispaivamaara"))
        permit.ryhti_modified_at = _datetime(props.get("aikaleima_utc"))
        permit.synced_at = now
    db.session.flush()
    return len(pairs)


def sync_permits(client: RyhtiClient | None = None, *, max_pages: int | None = None, now: datetime | None = None) -> int:
    client = client or RyhtiClient()
    now = now or _now()
    row = state("ryhti_permits")
    decided_since = (now - timedelta(days=PERMIT_MONTHS * 31)).date()
    scope = {fold(m) for m in municipalities_scope()}
    rows = 0
    cursor = row.cursor
    error = None
    batch: list[dict] = []
    try:
        for props in client.permits(decided_since=decided_since, since=row.cursor, max_pages=max_pages):
            stamp = props.get("aikaleima_utc")
            if stamp and (cursor is None or stamp > cursor):
                cursor = stamp
            if scope and fold(props.get("lupapaatoksen_kunta")) not in scope:
                continue
            batch.append(props)
            if len(batch) >= 500:
                rows += _upsert_permits(batch, now)
                batch = []
        rows += _upsert_permits(batch, now)
    except RegistryFetchError as exc:
        error = str(exc)[:1000]
        logger.warning("Ryhti permit sync stopped: %s", error)
    if error is None:
        row.cursor = cursor
    _finish(row, rows, error, now)
    db.session.commit()
    return rows


# --- Ryhti addresses ---------------------------------------------------------


def _keys_for_address(props: dict) -> list[str]:
    """Finnish and Swedish street names, every number of a range ("31-33")."""
    first = _int(props.get("number_part_of_address_number"))
    last = _int(props.get("number_part_of_address_number2"))
    if first is None:
        return []
    numbers = [first]
    if last and first < last <= first + 10:
        numbers = list(range(first, last + 1))
    keys: list[str] = []
    for street in (props.get("address_name_fin"), props.get("address_name_swe")):
        for key in (address_key(street, n) for n in numbers):
            if key and key not in keys:
                keys.append(key)
    return keys


def fetch_addresses(client: RyhtiClient | None = None, *, limit: int | None = None, now: datetime | None = None) -> int:
    """Addresses for up to `limit` buildings that have none (or changed)."""
    client = client or RyhtiClient()
    now = now or _now()
    limit = limit or int(_cfg("REGISTRY_ADDRESS_BATCH_LIMIT", 5000))
    row = state("ryhti_addresses")
    pending = (
        RegistryBuilding.query.filter(RegistryBuilding.addresses_fetched_at.is_(None))
        .order_by(RegistryBuilding.id)
        .limit(limit)
        .all()
    )
    done = 0
    error = None
    for start in range(0, len(pending), ADDRESS_BATCH):
        chunk = pending[start : start + ADDRESS_BATCH]
        try:
            found = client.addresses([b.building_key for b in chunk])
        except RegistryFetchError as exc:
            error = str(exc)[:1000]
            logger.warning("Ryhti address fetch stopped: %s", error)
            break
        by_building: dict[str, list[dict]] = {}
        for props in found:
            by_building.setdefault(props.get("building_key"), []).append(props)
        for building in chunk:
            rows = sorted(by_building.get(building.building_key, []), key=lambda p: p.get("address_number") or 99)
            keys: list[str] = []
            for props in rows:
                keys.extend(k for k in _keys_for_address(props) if k not in keys)
            if rows:
                building.address = _text(rows[0].get("address_fin"), 255)
                building.postal_code = _text(rows[0].get("postal_code"), 10)
                building.post_office = _text((rows[0].get("postal_office_fin") or "").title(), 100)
            building.address_keys = keys
            building.addresses_fetched_at = now
            done += 1
        db.session.commit()
    _finish(row, done, error, now)
    db.session.commit()
    return done


# --- PRH housing companies ---------------------------------------------------


def _current_name(company: dict) -> str | None:
    names = [n for n in company.get("names") or [] if str(n.get("type")) == "1" and not n.get("endDate")]
    names.sort(key=lambda n: n.get("registrationDate") or "", reverse=True)
    return _text(names[0].get("name"), 255) if names else None


def _address(company: dict) -> dict | None:
    """The street address (type 1) if any, otherwise the postal address."""
    addresses = sorted(company.get("addresses") or [], key=lambda a: int(a.get("type") or 9))
    for addr in addresses:
        if addr.get("street"):
            return addr
    return None


def _city(addr: dict) -> str | None:
    offices = addr.get("postOffices") or []
    finnish = [o for o in offices if str(o.get("languageCode")) == "1"] or offices
    return _text((finnish[0].get("city") or "").title(), 100) if finnish else None


def _upsert_company(company: dict, now: datetime) -> bool:
    business_id = ((company.get("businessId") or {}).get("value") or "").strip()
    name = _current_name(company)
    if not business_id or not name or str(company.get("tradeRegisterStatus")) != "1":
        return False
    if company.get("companySituations"):  # liquidation, bankruptcy, ...
        return False
    row = RegistryHousingCompany.query.filter_by(business_id=business_id).first()
    if row is None:
        row = RegistryHousingCompany(business_id=business_id, name=name)
        db.session.add(row)
    addr = _address(company) or {}
    street = _text(" ".join(p for p in (addr.get("street"), addr.get("buildingNumber"), addr.get("entrance")) if p), 255)
    row.name = name
    row.street = street
    row.postal_code = _text(addr.get("postCode"), 10)
    row.post_office = _city(addr) if addr else None
    row.municipality = row.post_office
    row.company_key = name_key(name, row.municipality)
    row.registration_date = _date((company.get("businessId") or {}).get("registrationDate"))
    row.prh_modified_at = _datetime(company.get("lastModified"))
    row.synced_at = now
    return True


def prh_locations() -> list[str]:
    """Configured municipalities, or every municipality that has buildings."""
    scope = municipalities_scope()
    if scope:
        return scope
    rows = db.session.query(RegistryBuilding.municipality).distinct().all()
    return sorted(m for (m,) in rows if m)


def sync_prh(
    client: PrhClient | None = None,
    *,
    max_locations: int | None = None,
    min_age_days: int = 7,
    now: datetime | None = None,
) -> int:
    """Housing companies for the municipalities synced longest ago (each at
    most weekly), `max_locations` per run."""
    client = client or PrhClient()
    now = now or _now()
    max_locations = max_locations or int(_cfg("REGISTRY_PRH_LOCATIONS_PER_RUN", 25))
    states = {s.name: s for s in RegistrySyncState.query.filter(RegistrySyncState.name.like("prh:%"))}

    def last_success(location: str) -> datetime:
        row = states.get(f"prh:{location}")
        value = row.last_success_at if row and row.last_success_at else datetime.min
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    due = [loc for loc in prh_locations() if last_success(loc) <= now - timedelta(days=min_age_days)]
    due.sort(key=last_success)
    total = 0
    for location in due[:max_locations]:
        row = state(f"prh:{location}")
        rows = 0
        error = None
        try:
            for company in client.housing_companies(location):
                if _upsert_company(company, now):
                    rows += 1
                if rows and rows % 500 == 0:
                    db.session.flush()
        except RegistryFetchError as exc:
            error = str(exc)[:1000]
            logger.warning("PRH sync for %s stopped: %s", location, error)
        _finish(row, rows, error, now)
        db.session.commit()
        total += rows
    return total


# --- linking -----------------------------------------------------------------

_NAME_PREFIX = re.compile(r"^(as oy|koy)\s+")
# "koulukatu 5", "koulukatu 5 a", "koulukatu 5 7" (= 5-7) after folding
_NAME_ADDRESS = re.compile(r"^(.*?[a-z])\s*(\d+)(?:\s+(\d+))?(?:\s*[a-z])?$")
_STREET_WORD = re.compile(r"^[a-z][a-z ]*[a-z]$")
_KEY_STREET = re.compile(r"\d+$")


def _name_body(company_name: str) -> str:
    from app.listings.housing import normalize_company_name

    return _NAME_PREFIX.sub("", normalize_company_name(company_name))


def _variants(text: str) -> list[str]:
    """The text, and without a leading word ("tampereen kauppakatu")."""
    words = text.split()
    return [text] + ([" ".join(words[1:])] if len(words) > 1 else [])


def _municipality_stems() -> dict[str, str]:
    from app.search.municipalities import MUNICIPALITIES_BY_REGION

    stems = {}
    for names in MUNICIPALITIES_BY_REGION.values():
        for name in names:
            folded = fold(name)
            if len(folded) >= 5:
                stems[folded[:-1]] = folded
    return stems


def name_town(company_name: str) -> str | None:
    """The municipality a name starts with in the genitive ("As Oy Kangasalan
    Pikontie 2" -> "kangasala", "Asunto Oy Tampereen Helmi" -> "tampere"):
    the company stands there even when registered at an office elsewhere.
    Approximate: irregular genitives (Turun, Lahden) are not recognised."""
    words = _name_body(company_name).split()
    if len(words) < 2 or not words[0].endswith("n"):
        return None
    first = words[0]
    for stem, town in _municipality_stems().items():
        if first.startswith(stem) and len(first) <= len(town) + 2:
            return town
    return None


def name_address_keys(company_name: str) -> list[str]:
    """"Asunto Oy Armas Lindgrenintie 19" -> ["armaslindgrenintie19", ...]; a
    range ("Koulukatu 5-7") gives every number; also without a leading word
    ("As Oy Tampereen Kauppakatu 5" -> "kauppakatu5")."""
    match = _NAME_ADDRESS.match(_name_body(company_name))
    if not match:
        return []
    first = int(match.group(2))
    last = int(match.group(3)) if match.group(3) else first
    numbers = list(range(first, last + 1)) if first < last <= first + 10 else [first]
    keys: list[str] = []
    for street in _variants(match.group(1)):
        for number in numbers:
            key = address_key(street, number)
            if key and key not in keys:
                keys.append(key)
    return keys


def name_street_keys(company_name: str) -> list[str]:
    """A name that is a street without a number: "As Oy Valkoseläntie" ->
    ["valkoselantie"], "As Oy Nokian Rantahaankatu" -> [..., "rantahaankatu"]."""
    body = _name_body(company_name)
    if not body or not _STREET_WORD.match(body):
        return []
    return [v.replace(" ", "") for v in _variants(body)]


def _listing_links() -> list[tuple[str, str, str | None, str | None]]:
    """(address key, folded municipality, company name, Y-tunnus) from the
    catalogue: a listing names the housing company of an address."""
    from app.listings.models import Listing

    rows = db.session.query(Listing.osoite, Listing.kaupunki, Listing.taloyhtio, Listing.ytunnus).filter(
        Listing.osoite.isnot(None), Listing.kaupunki.isnot(None)
    )
    links = []
    for osoite, kaupunki, taloyhtio, ytunnus in rows:
        key = _street_key(osoite)
        if key and (taloyhtio or ytunnus):
            links.append((key, fold(kaupunki), taloyhtio, ytunnus))
    return links


def link_companies(*, now: datetime | None = None) -> int:
    """Attach buildings to housing companies. Returns the linked count.
    In order, the first rule that gives a building a company wins:

    1. "name": the street address in the company name ("As Oy Härmälänkatu
       13") in the town the name names ("Kangasalan ..."), else in the
       company's own municipality, or anywhere in Finland when only one
       municipality has that address: the registered address is often the
       property manager's office in another town.
    2. "listing": a catalogue listing at the building's address names the
       company (by Y-tunnus, or by name in that municipality or uniquely).
    3. "prh_address": the company's registered street address in its own
       municipality, only when no other housing company is registered there
       (a shared address is a property manager's office, not the building)
       and the name does not place the company elsewhere (another town in
       the genitive, or another street address).
    4. "name_street": the company name is a street ("As Oy Valkoseläntie")
       and every residential building on that street in the company's
       municipality stands on one property.
    5. "property": the other buildings on the property (kiinteistötunnus) of
       a linked building, when the property has only one company.
    """
    from app.listings.housing import normalize_company_name, normalize_ytunnus

    rows = db.session.query(
        RegistryBuilding.id,
        RegistryBuilding.municipality,
        RegistryBuilding.address_keys,
        RegistryBuilding.property_id,
        RegistryBuilding.housing_company_id,
        RegistryBuilding.link_method,
    ).all()
    by_key: dict[str, list[tuple[int, str]]] = {}
    by_street: dict[tuple[str, str], set[int]] = {}
    by_property: dict[str, list[int]] = {}
    property_of: dict[int, str | None] = {}
    for building_id, municipality, keys, property_id, _company, _method in rows:
        town = fold(municipality)
        property_of[building_id] = property_id
        if property_id:
            by_property.setdefault(property_id, []).append(building_id)
        for key in keys or []:
            by_key.setdefault(key, []).append((building_id, town))
            by_street.setdefault((town, _KEY_STREET.sub("", key)), set()).add(building_id)

    companies = db.session.query(
        RegistryHousingCompany.id,
        RegistryHousingCompany.name,
        RegistryHousingCompany.municipality,
        RegistryHousingCompany.street,
        RegistryHousingCompany.business_id,
    ).all()
    shared = Counter(
        (fold(m), _street_key(street)) for _id, _name, m, street, _b in companies if _street_key(street)
    )

    def local_or_unique(candidates: list[tuple[int, str]], town: str) -> list[int]:
        local = [b for b, m in candidates if m == town]
        if local:
            return local
        return [b for b, _m in candidates] if len({m for _b, m in candidates}) == 1 else []

    assigned: dict[int, tuple[int, str]] = {}

    def assign(building_ids, company_id: int, method: str) -> None:
        for building_id in building_ids:
            assigned.setdefault(building_id, (company_id, method))

    towns = {company_id: name_town(name) for company_id, name, _m, _s, _b in companies}

    # 1. street address in the name, in the town the name names if any
    for company_id, name, municipality, _street, _b in companies:
        town = towns[company_id]
        for key in name_address_keys(name):
            candidates = by_key.get(key, [])
            if town:
                assign([b for b, m in candidates if m == town], company_id, "name")
            else:
                assign(local_or_unique(candidates, fold(municipality)), company_id, "name")

    # 2. catalogue listings
    by_business_id = {b: c for c, _n, _m, _s, b in companies}
    by_name: dict[str, list[tuple[int, str]]] = {}
    for company_id, name, municipality, _street, _b in companies:
        by_name.setdefault(normalize_company_name(name), []).append((company_id, fold(municipality)))
    for key, town, taloyhtio, ytunnus in _listing_links():
        company_id = by_business_id.get(normalize_ytunnus(ytunnus) or "")
        if company_id is None:
            named = by_name.get(normalize_company_name(taloyhtio), [])
            local = [c for c, m in named if m == town]
            if len(local) == 1:
                company_id = local[0]
            elif not local and len(named) == 1:
                company_id = named[0][0]
        if company_id:
            assign([b for b, m in by_key.get(key, []) if m == town], company_id, "listing")

    # 3. the company's own registered address, unless an office address
    for company_id, name, municipality, street, _b in companies:
        key = _street_key(street)
        if not key or shared[(fold(municipality), key)] != 1:
            continue
        town = towns[company_id]
        named = name_address_keys(name)
        # the name places the company elsewhere: the address is an office
        if (town and town != fold(municipality)) or (named and key not in named):
            continue
        assign([b for b, m in by_key.get(key, []) if m == fold(municipality)], company_id, "prh_address")

    # 4. the name is a street on which the buildings stand on one property
    street_claims: dict[tuple[str, str], set[int]] = {}
    for company_id, name, municipality, _street, _b in companies:
        for street_key in name_street_keys(name):
            street_claims.setdefault((fold(municipality), street_key), set()).add(company_id)
    for place, claimants in street_claims.items():
        buildings = by_street.get(place, set())
        if len(claimants) != 1 or not buildings:
            continue
        properties = {property_of.get(b) for b in buildings}
        if len(buildings) == 1 or (len(properties) == 1 and None not in properties):
            assign(buildings, next(iter(claimants)), "name_street")

    # 5. the rest of a linked building's property
    for property_id, buildings in by_property.items():
        companies_here = {assigned[b][0] for b in buildings if b in assigned}
        if len(companies_here) == 1:
            assign(buildings, companies_here.pop(), "property")

    changes = []
    for building_id, _m, _keys, _property, company, method in rows:
        new_company, new_method = assigned.get(building_id, (None, None))
        if (company, method) != (new_company, new_method):
            changes.append({"id": building_id, "housing_company_id": new_company, "link_method": new_method})
    for start in range(0, len(changes), 1000):
        db.session.bulk_update_mappings(RegistryBuilding, changes[start : start + 1000])
    db.session.commit()
    return len(assigned)
