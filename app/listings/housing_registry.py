"""One taloyhtiö across every source.

Every row a source gives (a catalogue listing, a PRH housing company, a Ryhti
building, and any source added later) is turned into a `Candidate` and
resolved to one `HousingCompany` with the same rules:

1. Y-tunnus.
2. Normalised name in the same municipality.
3. A street address in the same municipality, when the names do not
   contradict (one of them has no name, or the names are equal).

A candidate that finds nothing creates a company. When a candidate shows that
two companies are the same (its Y-tunnus finds one and its name another), they
are merged. Facts fill what the company does not know yet; nothing a source
said is overwritten by a weaker one.

Phase 1 of the shared pipeline (identify -> forecast -> categorise): this
module identifies. It changes nothing a customer sees.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func

from app.extensions import db
from app.listings.identity import business_id as normalize_business_id
from app.listings.identity import company_name_key, municipality_key, street_key
from app.listings.models import HousingCompany, HousingCompanyAddress, Listing

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Candidate:
    source: str
    name: str | None = None
    business_id: str | None = None
    municipality: str | None = None
    address: str | None = None
    address_keys: list[str] = field(default_factory=list)
    build_year: int | None = None
    building_type: str | None = None
    apartments: int | None = None
    lat: float | None = None
    lon: float | None = None

    @property
    def bid(self) -> str | None:
        return normalize_business_id(self.business_id)

    @property
    def name_key(self) -> str | None:
        return company_name_key(self.name) or None

    @property
    def town(self) -> str | None:
        return municipality_key(self.municipality) or None

    def keys(self) -> list[str]:
        keys = [k for k in self.address_keys if k]
        own = street_key(self.address)
        if own and own not in keys:
            keys.append(own)
        return keys

    def identifiable(self) -> bool:
        return bool(self.bid or self.name_key or (self.keys() and self.town))


# --- lookup ---------------------------------------------------------------------


def _by_business_id(bid: str | None) -> HousingCompany | None:
    return HousingCompany.query.filter_by(business_id=bid).first() if bid else None


def _by_name(name_key: str | None, town: str | None) -> HousingCompany | None:
    if not name_key or not town:
        return None
    return (
        HousingCompany.query.filter_by(name_key=name_key, municipality_key=town)
        .order_by(HousingCompany.id)
        .first()
    )


def _by_address(keys: list[str], town: str | None, name_key: str | None) -> HousingCompany | None:
    if not keys or not town:
        return None
    rows = (
        db.session.query(HousingCompany)
        .join(HousingCompanyAddress, HousingCompanyAddress.housing_id == HousingCompany.id)
        .filter(HousingCompanyAddress.address_key.in_(keys), HousingCompanyAddress.municipality_key == town)
        .order_by(HousingCompany.id)
        .all()
    )
    for company in rows:
        # an address shared by two differently named companies is not proof
        if not name_key or not company.name_key or company.name_key == name_key:
            return company
    return None


def _compatible(a: HousingCompany, b: HousingCompany) -> bool:
    if a.business_id and b.business_id and a.business_id != b.business_id:
        return False
    if a.name_key and b.name_key and a.name_key != b.name_key:
        return False
    return True


# --- write ----------------------------------------------------------------------


BUILDING_AUTHORITIES = ("ryhti",)


def _add_source(company: HousingCompany, source: str) -> None:
    sources = list(company.sources or [])
    if source not in sources:
        sources.append(source)
        company.sources = sources


def _absorb(company: HousingCompany, candidate: Candidate, now: datetime) -> None:
    """Fill what the company does not know yet from the candidate."""
    if candidate.bid and not company.business_id and not _by_business_id(candidate.bid):
        company.business_id = candidate.bid
    if candidate.name and not company.name:
        company.name = candidate.name[:255]
        company.name_key = candidate.name_key
    if candidate.municipality and not company.municipality:
        company.municipality = candidate.municipality[:100]
        company.municipality_key = candidate.town
    if candidate.address and not company.address:
        company.address = candidate.address[:255]
    # the building register is the authority on the building itself; other
    # sources only fill what it has not said
    authoritative = candidate.source in BUILDING_AUTHORITIES
    for attr in ("build_year", "building_type", "apartments", "lat", "lon"):
        value = getattr(candidate, attr)
        if value is not None and (getattr(company, attr) is None or authoritative):
            setattr(company, attr, value)
    town = candidate.town or company.municipality_key
    known = {a.address_key for a in company.addresses}
    for key in candidate.keys():
        if key not in known:
            company.addresses.append(HousingCompanyAddress(address_key=key[:120], municipality_key=town))
            known.add(key)
    _add_source(company, candidate.source)
    company.updated_at = now


def merge_companies(keep: HousingCompany, drop: HousingCompany, *, now: datetime | None = None) -> HousingCompany:
    """`drop` is the same taloyhtiö as `keep`: move every link and fact over."""
    from app.registry.models import RegistryBuilding, RegistryHousingCompany

    now = now or _now()
    if keep.id == drop.id:
        return keep
    for model in (Listing, RegistryHousingCompany, RegistryBuilding):
        model.query.filter_by(housing_id=drop.id).update({"housing_id": keep.id}, synchronize_session=False)
    bid = drop.business_id
    drop.business_id = None  # unique: free it before keep takes it
    db.session.flush()
    _absorb(
        keep,
        Candidate(
            source=(drop.sources or ["merge"])[0], name=drop.name, business_id=bid, municipality=drop.municipality,
            address=drop.address, address_keys=[a.address_key for a in drop.addresses], build_year=drop.build_year,
            building_type=drop.building_type, apartments=drop.apartments, lat=drop.lat, lon=drop.lon,
        ),
        now,
    )
    for source in drop.sources or []:
        _add_source(keep, source)
    db.session.delete(drop)
    db.session.flush()
    return keep


def resolve(candidate: Candidate, *, now: datetime | None = None) -> HousingCompany | None:
    """The housing company of a source row, created when none matches. None
    when the row says nothing that identifies a company."""
    now = now or _now()
    if not candidate.identifiable():
        return None
    by_bid = _by_business_id(candidate.bid)
    by_name = _by_name(candidate.name_key, candidate.town)
    if by_name is not None and candidate.bid and by_name.business_id and by_name.business_id != candidate.bid:
        by_name = None  # same name in the same town, another Y-tunnus: another company
    if by_bid is not None and by_name is not None and by_name.id != by_bid.id:
        # a name with another Y-tunnus is another company; without one it is this one
        if _compatible(by_bid, by_name):
            merge_companies(by_bid, by_name, now=now)
        by_name = None
    company = by_bid or by_name
    if company is None and not (candidate.bid and _by_business_id(candidate.bid)):
        company = _by_address(candidate.keys(), candidate.town, candidate.name_key)
        if company is not None and candidate.bid and company.business_id and company.business_id != candidate.bid:
            company = None
    if company is not None and candidate.name_key and company.name_key is None:
        # a nameless company (a register building) is being named: another
        # company may already carry the name in this town
        named = _by_name(candidate.name_key, candidate.town)
        if named is not None and named.id != company.id and _compatible(named, company):
            company = merge_companies(named, company, now=now)
    if company is None:
        company = HousingCompany(created_at=now, updated_at=now, sources=[])
        db.session.add(company)
    _absorb(company, candidate, now)
    db.session.flush()
    return company


# --- sources --------------------------------------------------------------------


def listing_candidate(listing: Listing) -> Candidate:
    return Candidate(
        source=listing.source or "import",
        name=listing.taloyhtio,
        business_id=listing.ytunnus,
        municipality=listing.kaupunki,
        address=listing.osoite,
        build_year=listing.build_year,
        building_type=listing.building_type,
        lat=listing.lat if listing.geo_precision in ("source", "address") else None,
        lon=listing.lon if listing.geo_precision in ("source", "address") else None,
    )


def resolve_listings(*, limit: int = 2000, now: datetime | None = None) -> int:
    """Listings not resolved yet (new, or renamed since). A register estimate
    (source "ryhti") takes its building's company."""
    from app.registry.models import RegistryBuilding

    now = now or _now()
    listings = Listing.query.filter(Listing.housing_id.is_(None)).order_by(Listing.id).limit(limit).all()
    done = 0
    for listing in listings:
        company = None
        if listing.source == "ryhti":
            building = RegistryBuilding.query.filter_by(building_key=listing.external_id).first()
            if building is not None and building.housing_id:
                company = db.session.get(HousingCompany, building.housing_id)
        if company is None:
            company = resolve(listing_candidate(listing), now=now)
        if company is not None:
            listing.housing_id = company.id
            done += 1
    db.session.commit()
    return done


def resolve_registry(*, limit: int = 20000, now: datetime | None = None) -> dict:
    """PRH housing companies (with the buildings linked to them), then the
    buildings no company claims, as nameless companies known by address."""
    from app.registry.models import RegistryBuilding, RegistryHousingCompany

    now = now or _now()
    counts = {"companies": 0, "buildings": 0}
    companies = (
        RegistryHousingCompany.query.filter(RegistryHousingCompany.housing_id.is_(None))
        .order_by(RegistryHousingCompany.id)
        .limit(limit)
        .all()
    )
    for row in companies:
        buildings = list(row.buildings)
        first = buildings[0] if buildings else None
        company = resolve(
            Candidate(
                source="prh",
                name=row.name,
                business_id=row.business_id,
                # the buildings' municipality: PRH's address is often the manager's office
                municipality=(first.municipality if first else None) or row.municipality,
                address=first.address if first else None,
                address_keys=[k for b in buildings for k in (b.address_keys or [])],
                build_year=min((b.completion_year for b in buildings if b.completion_year), default=None),
                building_type=first.building_type if first else None,
                apartments=sum(b.apartments or 0 for b in buildings) or None,
                lat=first.lat if first else None,
                lon=first.lon if first else None,
            ),
            now=now,
        )
        if company is None:
            continue
        row.housing_id = company.id
        for building in buildings:
            building.housing_id = company.id
            _add_source(company, "ryhti")
        counts["companies"] += 1
        if counts["companies"] % 500 == 0:
            db.session.commit()
    db.session.commit()
    buildings = (
        RegistryBuilding.query.filter(
            RegistryBuilding.housing_id.is_(None), RegistryBuilding.addresses_fetched_at.isnot(None)
        )
        .order_by(RegistryBuilding.id)
        .limit(limit)
        .all()
    )
    for building in buildings:
        if building.housing_company is not None and building.housing_company.housing_id:
            building.housing_id = building.housing_company.housing_id
            continue
        company = resolve(
            Candidate(
                source="ryhti", municipality=building.municipality, address=building.address,
                address_keys=list(building.address_keys or []), build_year=building.completion_year,
                building_type=building.building_type, apartments=building.apartments,
                lat=building.lat, lon=building.lon,
            ),
            now=now,
        )
        if company is not None:
            building.housing_id = company.id
            counts["buildings"] += 1
        if counts["buildings"] % 500 == 0:
            db.session.commit()
    db.session.commit()
    return counts


# --- overview -------------------------------------------------------------------


def housing_overview(search: str | None = None, *, limit: int = 30) -> dict:
    """Counts for the superadmin: companies, how many each source knows, how
    many several sources know, and a search over them."""
    from app.registry.models import RegistryBuilding, RegistryHousingCompany

    total = HousingCompany.query.count()
    named = HousingCompany.query.filter(HousingCompany.name_key.isnot(None)).count()
    with_bid = HousingCompany.query.filter(HousingCompany.business_id.isnot(None)).count()
    in_listings = db.session.query(func.count(func.distinct(Listing.housing_id))).filter(Listing.housing_id.isnot(None)).scalar() or 0
    in_prh = (
        db.session.query(func.count(func.distinct(RegistryHousingCompany.housing_id)))
        .filter(RegistryHousingCompany.housing_id.isnot(None))
        .scalar()
        or 0
    )
    in_ryhti = (
        db.session.query(func.count(func.distinct(RegistryBuilding.housing_id)))
        .filter(RegistryBuilding.housing_id.isnot(None))
        .scalar()
        or 0
    )
    listing_ids = {i for (i,) in db.session.query(Listing.housing_id).filter(Listing.housing_id.isnot(None)).distinct()}
    register_ids = {
        i for (i,) in db.session.query(RegistryBuilding.housing_id).filter(RegistryBuilding.housing_id.isnot(None)).distinct()
    } | {
        i
        for (i,) in db.session.query(RegistryHousingCompany.housing_id)
        .filter(RegistryHousingCompany.housing_id.isnot(None))
        .distinct()
    }
    unresolved = Listing.query.filter(Listing.housing_id.is_(None)).count()
    results = []
    term = " ".join((search or "").split())
    if term:
        like = f"%{term.lower()}%"
        for company in (
            HousingCompany.query.filter(
                db.or_(func.lower(HousingCompany.name).like(like), func.lower(HousingCompany.address).like(like),
                       HousingCompany.business_id == term)
            )
            .order_by(HousingCompany.name)
            .limit(limit)
        ):
            results.append(
                {
                    "company": company,
                    "listings": Listing.query.filter_by(housing_id=company.id).count(),
                    "buildings": RegistryBuilding.query.filter_by(housing_id=company.id).count(),
                }
            )
    return {
        "total": total,
        "named": named,
        "with_business_id": with_bid,
        "in_listings": in_listings,
        "in_prh": in_prh,
        "in_ryhti": in_ryhti,
        "listings_and_registers": len(listing_ids & register_ids),
        "unresolved_listings": unresolved,
        "results": results,
        "search": term,
    }
