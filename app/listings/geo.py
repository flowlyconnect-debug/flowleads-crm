"""Where a target is on the map.

Positions, best first:
    1. ``source``: coordinates given by the import row (lat/lon columns);
    2. ``address``: the street address geocoded by the scheduler job
       ``geocode_listings`` (only with GEOCODING_ENABLED, see app/config.py);
    3. ``city`` / ``region``: the centre of the town or the maakunta
       (app/listings/geo_data.py), spread a little so targets of one town do
       not sit on top of each other. The map marks these as approximate.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone

from app.extensions import db
from app.listings.geo_data import CITY_CENTRES, REGION_CENTRES
from app.listings.housing import fold

logger = logging.getLogger(__name__)

# Finland incl. Åland, generously: coordinates outside are input errors
_LAT_RANGE = (59.3, 70.2)
_LON_RANGE = (19.0, 31.7)
MAX_MAP_POINTS = 2000


def valid_position(lat, lon) -> tuple[float, float] | None:
    try:
        lat_f = float(str(lat).replace(",", "."))
        lon_f = float(str(lon).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if _LAT_RANGE[0] <= lat_f <= _LAT_RANGE[1] and _LON_RANGE[0] <= lon_f <= _LON_RANGE[1]:
        return round(lat_f, 6), round(lon_f, 6)
    return None


def _spread(listing_id: int, radius: float) -> tuple[float, float]:
    """A stable small offset per listing (the same every page load)."""
    digest = hashlib.sha1(str(listing_id).encode()).digest()
    dx = (digest[0] / 255.0 - 0.5) * 2 * radius
    dy = (digest[1] / 255.0 - 0.5) * 2 * radius
    return dy, dx * 2  # a degree of longitude is about half as long here


def position(listing) -> dict | None:
    """{"lat", "lon", "precision"} for the listing, or None when even the
    region is unknown."""
    if listing.lat is not None and listing.lon is not None:
        exact = valid_position(listing.lat, listing.lon)
        if exact:
            return {"lat": exact[0], "lon": exact[1], "precision": listing.geo_precision or "source"}
    centre = CITY_CENTRES.get(fold(listing.kaupunki)) if listing.kaupunki else None
    precision, radius = "city", 0.012
    if centre is None:
        centre = REGION_CENTRES.get(listing.maakunta or "")
        precision, radius = "region", 0.05
    if centre is None:
        return None
    dy, dx = _spread(listing.id or 0, radius)
    return {"lat": round(centre[0] + dy, 5), "lon": round(centre[1] + dx, 5), "precision": precision}


PRECISION_LABELS = {
    "source": "tarkka sijainti",
    "address": "osoitteen mukaan",
    "city": "kunnan keskusta (likimääräinen)",
    "region": "maakunnan keskus (likimääräinen)",
}


def map_points(rows) -> list[dict]:
    """Map markers for Kohteet rows (target_services.target_row dicts)."""
    points = []
    for row in rows[:MAX_MAP_POINTS]:
        listing = row["listing"]
        where = next((position(l) for l in row["company_listings"] if l.lat is not None), None) or position(listing)
        if where is None:
            continue
        points.append(
            {
                **where,
                "precision_label": PRECISION_LABELS.get(where["precision"], ""),
                "id": row["match"].id,
                "listing_id": listing.id,
                "title": row["title"],
                "location": row["location"],
                "score": row["score"],
                "level": row["score_level"],
                "renovations": [
                    " ".join(p for p in (r.remonttityyppi, r.timing or "") if p) for r in row["renovations"][:3]
                ],
                "manager": row["contact"].isannoitsija or "",
                "has_contact": bool(row["contact"].has_contact),
            }
        )
    return points


# --- address geocoding (optional) ------------------------------------------------


def _geocode(address: str, *, session=None) -> tuple[float, float] | None:
    import requests
    from flask import current_app

    base_url = (current_app.config.get("APP_BASE_URL") or "").rstrip("/")
    headers = {
        # Nominatim's usage policy asks for an identifying User-Agent
        "User-Agent": "FlowLeads-CRM/1.0" + (f" (+{base_url})" if base_url else ""),
        "Accept-Language": "fi",
    }
    params = {"q": address, "format": "jsonv2", "limit": 1, "countrycodes": "fi"}
    response = (session or requests).get(current_app.config["GEOCODER_URL"], params=params, headers=headers, timeout=10)
    response.raise_for_status()
    results = response.json()
    if not isinstance(results, list) or not results:
        return None
    return valid_position(results[0].get("lat"), results[0].get("lon"))


def geocode_listings(*, limit: int | None = None, now: datetime | None = None, sleep=time.sleep, session=None) -> int:
    """Geocode the street address of matched, active listings that have none.
    One request a second (Nominatim policy); every listing is tried once
    (geocoded_at), a failure is not retried. Returns how many got a position."""
    from flask import current_app

    from app.listings.models import Listing, ListingMatch

    if not current_app.config.get("GEOCODING_ENABLED"):
        return 0
    now = now or datetime.now(timezone.utc)
    limit = limit or int(current_app.config.get("GEOCODING_BATCH", 40))
    matched = db.session.query(ListingMatch.listing_id)
    pending = (
        Listing.query.filter(
            Listing.is_active.is_(True),
            Listing.lat.is_(None),
            Listing.geocoded_at.is_(None),
            Listing.osoite.isnot(None),
            Listing.id.in_(matched),
        )
        .order_by(Listing.id.desc())
        .limit(limit)
        .all()
    )
    found = 0
    for index, listing in enumerate(pending):
        if index:
            sleep(1.1)
        address = ", ".join(p for p in (listing.osoite, listing.kaupunki, "Finland") if p)
        listing.geocoded_at = now
        try:
            where = _geocode(address, session=session)
        except Exception as exc:  # network trouble: try the rest next time
            logger.warning("Geocoding failed for listing %s: %s", listing.id, exc)
            listing.geocoded_at = None
            db.session.commit()
            break
        if where:
            listing.lat, listing.lon = where
            listing.geo_precision = "address"
            found += 1
        db.session.commit()
    return found
