"""Register signals -> catalogue targets marked "Ennuste" (Listing.is_estimate).

Two locks keep this away from customers until it is wanted:

1. Nothing is published unless REGISTRY_TARGETS_PUBLISH is on.
2. A published estimate is matched only for organizations that have the
   "registry_targets" module (superadmin, /admin/customers), see
   app/listings/services.py::estimates_allowed. No organization has it by
   default.

One listing per building (source "ryhti", external_id = Ryhti building key),
named after the linked housing company. A building without a company is
published only for an alteration permit (the strongest signal), unless
REGISTRY_PUBLISH_UNLINKED is on: a salesperson needs a company to call.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.listings.housing import fold
from app.listings.models import Listing, ListingRenovation
from app.registry.models import RegistryBuilding, RegistryHousingCompany, RegistrySignal

SOURCE = "ryhti"
CHUNK = 1000
ESTIMATE_NOTE = "Arvio julkisista rekistereistä (Syke Ryhti, PRH), ei varmistettu tieto."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def publish_enabled() -> bool:
    return bool(current_app.config.get("REGISTRY_TARGETS_PUBLISH"))


def _regions() -> dict[str, str]:
    from app.search.municipalities import MUNICIPALITIES_BY_REGION

    return {fold(name): region for region, names in MUNICIPALITIES_BY_REGION.items() for name in names}


def desired_listing(building: RegistryBuilding, company: RegistryHousingCompany | None, signals, regions, *, unlinked_ok: bool) -> dict | None:
    """What the building's listing should hold, or None (no listing)."""
    region = regions.get(fold(building.municipality))
    if not region or not building.address:
        return None
    if company is None and not unlinked_ok:
        signals = [s for s in signals if s.kind == "permit"]
    if not signals:
        return None
    renovations = sorted(
        (
            {
                "remonttityyppi": s.renovation_type,
                "confidence": s.confidence,
                "timing": str(s.due_year) if s.due_year else None,
                "timing_year": s.due_year,
                "evidence": f"{s.reason} {ESTIMATE_NOTE}",
            }
            for s in signals
        ),
        key=lambda r: (r["remonttityyppi"], -r["confidence"]),
    )
    # one row per type: the most confident signal
    by_type: dict[str, dict] = {}
    for item in renovations:
        by_type.setdefault(item["remonttityyppi"], item)
    return {
        "maakunta": region,
        "kaupunki": building.municipality,
        "osoite": building.address,
        "build_year": building.completion_year,
        "building_type": building.building_type,
        "taloyhtio": company.name if company else None,
        "ytunnus": company.business_id if company else None,
        "lat": building.lat,
        "lon": building.lon,
        "description": "\n".join([s.reason for s in signals] + [ESTIMATE_NOTE]),
        "renovations": list(by_type.values()),
    }


def _hash(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _apply(listing: Listing, fields: dict, now: datetime) -> None:
    for key in ("maakunta", "kaupunki", "osoite", "build_year", "building_type", "taloyhtio", "ytunnus", "lat", "lon", "description"):
        setattr(listing, key, fields[key])
    listing.geo_precision = "source" if fields["lat"] is not None else None
    listing.is_estimate = True
    listing.is_active = True
    listing.removed_at = None
    listing.last_seen_at = now
    listing.analysis_status = "done"
    listing.analyzed_at = now
    listing.renovations = [ListingRenovation(**item) for item in fields["renovations"]]


def _retire(listing: Listing, now: datetime) -> None:
    listing.is_active = False
    listing.removed_at = now
    listing.renovations = []
    listing.content_hash = None


def publish_targets(*, now: datetime | None = None) -> dict:
    """Create, update and retire the estimate listings; rematch what changed.
    Does nothing unless REGISTRY_TARGETS_PUBLISH is on."""
    from app.listings.services import match_listing

    if not publish_enabled():
        return {"skipped": True}
    now = now or _now()
    regions = _regions()
    unlinked_ok = bool(current_app.config.get("REGISTRY_PUBLISH_UNLINKED"))
    counts = {"created": 0, "updated": 0, "retired": 0, "unchanged": 0}
    published: set[str] = set()
    last_id = 0
    while True:
        buildings = (
            RegistryBuilding.query.filter(RegistryBuilding.id > last_id).order_by(RegistryBuilding.id).limit(CHUNK).all()
        )
        if not buildings:
            break
        last_id = buildings[-1].id
        signals: dict[int, list[RegistrySignal]] = {}
        for signal in RegistrySignal.query.filter(RegistrySignal.building_id.in_([b.id for b in buildings])):
            signals.setdefault(signal.building_id, []).append(signal)
        existing = {
            listing.external_id: listing
            for listing in Listing.query.filter(
                Listing.source == SOURCE, Listing.external_id.in_([b.building_key for b in buildings])
            )
        }
        changed: list[Listing] = []
        for building in buildings:
            listing = existing.get(building.building_key)
            fields = desired_listing(
                building, building.housing_company, signals.get(building.id, []), regions, unlinked_ok=unlinked_ok
            )
            if fields is None:
                if listing is not None and listing.is_active:
                    _retire(listing, now)
                    changed.append(listing)
                    counts["retired"] += 1
                continue
            published.add(building.building_key)
            digest = _hash(fields)
            if listing is not None and listing.is_active and listing.content_hash == digest:
                counts["unchanged"] += 1
                continue
            if listing is None:
                listing = Listing(source=SOURCE, external_id=building.building_key, published_at=now, first_seen_at=now)
                db.session.add(listing)
                counts["created"] += 1
            else:
                counts["updated"] += 1
            _apply(listing, fields, now)
            listing.content_hash = digest
            changed.append(listing)
        db.session.flush()
        for listing in changed:
            match_listing(listing, now=now)
        db.session.commit()
    # listings whose building left the registers
    for listing in Listing.query.filter(Listing.source == SOURCE, Listing.is_active.is_(True)):
        if listing.external_id not in published:
            _retire(listing, now)
            match_listing(listing, now=now)
            counts["retired"] += 1
    db.session.commit()
    return counts
