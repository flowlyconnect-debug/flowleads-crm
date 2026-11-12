"""Listing catalogue services: crawl planning, ingest, analysis and contact
queues, and matching listings to search profiles -> leads.

Flow (see docs/ARKKITEHTUURI_KOHDEKATALOGI.md):
    n8n Crawler   -> get_crawl_plan / ingest_page        (listings table)
    n8n Analyzer  -> claim_analysis / save_analysis      (renovations + match_listing)
    CRM scheduler -> rematch_profiles / materialize_pending_matches (leads)
    n8n Enricher  -> claim_contacts / save_contacts      (contacts -> leads)
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import and_, func, or_, update

from app.extensions import db
from app.listings.models import CrawlState, Listing, ListingMatch, ListingRenovation
from app.search.constants import ALL_FINLAND_REGION, BUILDING_TYPES, FINNISH_REGIONS, REMONTTITYYPIT
from app.search.models import SearchProfile

logger = logging.getLogger(__name__)

SOURCE = "oikotie"
# Listings that did not come from the Oikotie crawler (CSV / API import, e.g. a
# licensed data feed). They live in the same catalogue and match the same
# profiles; only the crawl state and "removed from Oikotie" logic ignore them.
IMPORT_SOURCE = "import"
PAGE_SIZE = 24

REGION_SLUGS: dict[str, str] = {
    "Uusimaa": "uusimaa",
    "Varsinais-Suomi": "varsinais-suomi",
    "Satakunta": "satakunta",
    "Kanta-Häme": "kanta-hame",
    "Pirkanmaa": "pirkanmaa",
    "Päijät-Häme": "paijat-hame",
    "Kymenlaakso": "kymenlaakso",
    "Etelä-Karjala": "etela-karjala",
    "Etelä-Savo": "etela-savo",
    "Pohjois-Savo": "pohjois-savo",
    "Pohjois-Karjala": "pohjois-karjala",
    "Keski-Suomi": "keski-suomi",
    "Etelä-Pohjanmaa": "etela-pohjanmaa",
    "Pohjanmaa": "pohjanmaa",
    "Keski-Pohjanmaa": "keski-pohjanmaa",
    "Pohjois-Pohjanmaa": "pohjois-pohjanmaa",
    "Kainuu": "kainuu",
    "Lappi": "lappi",
}

DEFAULTS = {
    "LISTING_CRAWL_REGIONS": "all",  # "all" | "active" (only regions used by active profiles)
    "LISTING_INCREMENTAL_MAX_PAGES": 10,
    "LISTING_FULL_PAGES_PER_RUN": 30,
    "LISTING_FULL_REGIONS_PER_RUN": 2,
    "LISTING_FULL_REFRESH_DAYS": 7,
    "LISTING_MATCH_HISTORY_DAYS": 30,
    "LISTING_MAX_ATTEMPTS": 3,
    "LISTING_CLAIM_TIMEOUT_MINUTES": 30,
    "LISTING_RETENTION_DAYS": 90,  # removed listings are deleted after this
    "LISTING_STALE_HOURS": 6,  # no successful crawl / analysis for this long = alert
    "LISTING_ALERT_INTERVAL_HOURS": 24,
}


class ListingServiceError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _cfg(name: str):
    try:
        value = current_app.config.get(name, DEFAULTS[name])
    except RuntimeError:
        value = DEFAULTS[name]
    if isinstance(DEFAULTS[name], int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return DEFAULTS[name]
    return value


def auto_create_leads() -> bool:
    """False (default): matches wait on the Kohteet page until a user moves
    them to leads. True: the old automatic match -> lead conversion."""
    try:
        return bool(current_app.config.get("LISTING_AUTO_CREATE_LEADS", False))
    except RuntimeError:
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clean(value, max_len: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len] if max_len else text


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


def _parse_year(value, *, low: int = 1700, high: int = 2100) -> int | None:
    """First plausible year in a number or free text ("PTS 2027-2029" -> 2027)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        year = int(value)
        return year if low <= year <= high else None
    match = _YEAR_RE.search(str(value))
    if not match:
        return None
    year = int(match.group(1))
    return year if low <= year <= high else None


_BUILDING_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("kerrostalo", "Kerrostalo"),
    ("luhtitalo", "Kerrostalo"),
    ("rivitalo", "Rivitalo"),
    ("paritalo", "Paritalo"),
    ("omakotitalo", "Omakotitalo"),
    ("erillistalo", "Omakotitalo"),
)
# Oikotie's numeric buildingType codes on a listing card.
_BUILDING_TYPE_CODES = {1: "Kerrostalo", 2: "Rivitalo", 4: "Omakotitalo", 8: "Paritalo"}


def _parse_building_type(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _BUILDING_TYPE_CODES.get(int(value))
    text = str(value).strip().lower()
    if not text:
        return None
    if text.isdigit():
        return _BUILDING_TYPE_CODES.get(int(text))
    for keyword, label in _BUILDING_TYPE_KEYWORDS:
        if keyword in text:
            return label
    for label in BUILDING_TYPES:
        if text == label.lower():
            return label
    return "Muu"


def _content_hash(description: str | None) -> str:
    return hashlib.sha256((description or "").strip().encode("utf-8")).hexdigest()


def profile_regions(profile: SearchProfile) -> list[str]:
    regions = list(profile.regions or [])
    if ALL_FINLAND_REGION in regions:
        return list(FINNISH_REGIONS)
    return [r for r in regions if r in REGION_SLUGS]


def _active_profiles():
    return SearchProfile.query.filter(
        SearchProfile.is_active.is_(True), SearchProfile.source == SOURCE
    ).all()


# ---------------------------------------------------------------------------
# Crawl plan + ingest (n8n Crawler)
# ---------------------------------------------------------------------------


def _crawl_regions() -> list[str]:
    if str(_cfg("LISTING_CRAWL_REGIONS")).lower() == "active":
        wanted: set[str] = set()
        for profile in _active_profiles():
            wanted.update(profile_regions(profile))
        return [r for r in FINNISH_REGIONS if r in wanted]
    return list(FINNISH_REGIONS)


def _state(maakunta: str) -> CrawlState:
    state = CrawlState.query.filter_by(source=SOURCE, maakunta=maakunta).first()
    if state is None:
        state = CrawlState(source=SOURCE, maakunta=maakunta, full_offset=0)
        db.session.add(state)
        db.session.flush()
    return state


def _full_in_progress(state: CrawlState) -> bool:
    started = _aware(state.full_started_at)
    completed = _aware(state.full_completed_at)
    return started is not None and (completed is None or completed < started)


def get_crawl_plan(*, now: datetime | None = None) -> list[dict]:
    """Tasks for one Crawler run: an incremental task (newest first) for every
    crawled region, plus a slice of the full pass for a few regions."""
    now = now or _now()
    regions = _crawl_regions()
    states = {r: _state(r) for r in regions}

    tasks = [
        {
            "maakunta": r,
            "slug": REGION_SLUGS[r],
            "mode": "incremental",
            "offset": 0,
            "max_pages": _cfg("LISTING_INCREMENTAL_MAX_PAGES"),
        }
        for r in regions
    ]

    refresh_cutoff = now - timedelta(days=_cfg("LISTING_FULL_REFRESH_DAYS"))
    in_progress = [r for r in regions if _full_in_progress(states[r])]
    due = [
        r
        for r in regions
        if r not in in_progress
        and (
            states[r].full_completed_at is None
            or _aware(states[r].full_completed_at) < refresh_cutoff
        )
    ]
    # never-crawled regions first, then the stalest refreshes
    due.sort(key=lambda r: (_aware(states[r].full_completed_at) or datetime.min.replace(tzinfo=timezone.utc)))

    for r in (in_progress + due)[: _cfg("LISTING_FULL_REGIONS_PER_RUN")]:
        state = states[r]
        if not _full_in_progress(state):
            state.prev_full_started_at = state.full_started_at
            state.full_started_at = now
            state.full_offset = 0
        tasks.append(
            {
                "maakunta": r,
                "slug": REGION_SLUGS[r],
                "mode": "full",
                "offset": state.full_offset or 0,
                "max_pages": _cfg("LISTING_FULL_PAGES_PER_RUN"),
            }
        )
    db.session.commit()
    return tasks


_CARD_FIELDS = (
    "url",
    "kaupunki",
    "osoite",
    "hinta",
    "alue",
    "description",
    "published_at",
    "build_year",
    "building_type",
)


def _normalize_listing_payload(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    external_id = _clean(raw.get("external_id"), 50)
    if not external_id:
        return None
    return {
        "external_id": external_id,
        "url": _clean(raw.get("url"), 500),
        "kaupunki": _clean(raw.get("kaupunki"), 100),
        "osoite": _clean(raw.get("osoite"), 255),
        "hinta": _clean(raw.get("hinta"), 50),
        "alue": _clean(raw.get("alue"), 50),
        "description": _clean(raw.get("description"), 5000),
        "published_at": _parse_datetime(raw.get("published_at")),
        "build_year": _parse_year(raw.get("build_year")),
        "building_type": _parse_building_type(raw.get("building_type")),
    }


def ingest_page(
    *,
    maakunta: str,
    mode: str,
    offset: int,
    page_in_run: int,
    listings: list,
    error: str | None = None,
    now: datetime | None = None,
) -> dict:
    if maakunta not in REGION_SLUGS:
        raise ListingServiceError(f"Unknown maakunta: {maakunta}")
    if mode not in ("incremental", "full"):
        raise ListingServiceError("mode must be incremental or full")
    if not isinstance(listings, list):
        raise ListingServiceError("listings must be a list")

    now = now or _now()
    state = _state(maakunta)

    if error:
        state.last_error = str(error)[:2000]
        state.last_error_at = now
        db.session.commit()
        return {"created": 0, "updated": 0, "removed": 0, "continue": False, "next_offset": offset}

    rows = [r for r in (_normalize_listing_payload(x) for x in listings) if r]
    ids = [r["external_id"] for r in rows]
    existing = {
        l.external_id: l
        for l in Listing.query.filter(Listing.source == SOURCE, Listing.external_id.in_(ids)).all()
    } if ids else {}

    created = updated = 0
    for r in rows:
        digest = _content_hash(r["description"])
        listing = existing.get(r["external_id"])
        if listing is None:
            listing = Listing(
                source=SOURCE,
                external_id=r["external_id"],
                maakunta=maakunta,
                first_seen_at=now,
                last_seen_at=now,
                content_hash=digest,
                analysis_status="pending",
                **{k: r[k] for k in _CARD_FIELDS},
            )
            db.session.add(listing)
            existing[r["external_id"]] = listing
            created += 1
            continue

        listing.last_seen_at = now
        if not listing.is_active:
            listing.is_active = True
            listing.removed_at = None
        for key in _CARD_FIELDS:
            if r[key] is not None:
                setattr(listing, key, r[key])
        listing.maakunta = maakunta
        if listing.content_hash != digest:
            listing.content_hash = digest
            if listing.analysis_status in ("done", "failed"):
                # Listing text changed (e.g. new renovation info): analyse again.
                listing.analysis_status = "pending"
                listing.analysis_attempts = 0
        updated += 1

    removed = 0
    if mode == "incremental":
        state.last_incremental_at = now
    else:
        state.full_offset = offset + len(rows)
        if len(listings) < PAGE_SIZE:
            removed = _complete_full_pass(state, now)

    state.last_error = None
    db.session.commit()

    if len(listings) < PAGE_SIZE:
        keep_going = False
    elif mode == "incremental":
        keep_going = created > 0 and page_in_run < _cfg("LISTING_INCREMENTAL_MAX_PAGES")
    else:
        keep_going = page_in_run < _cfg("LISTING_FULL_PAGES_PER_RUN")

    return {
        "created": created,
        "updated": updated,
        "removed": removed,
        "continue": keep_going,
        "next_offset": offset + PAGE_SIZE,
    }


def _complete_full_pass(state: CrawlState, now: datetime) -> int:
    """A listing is considered gone from Oikotie only when it was missed by two
    full passes in a row: offsets shift while a pass is in progress, so one
    pass alone can skip a still-active listing."""
    state.full_completed_at = now
    state.full_offset = 0
    cutoff = _aware(state.prev_full_started_at)
    if cutoff is None:
        return 0
    stale = Listing.query.filter(
        Listing.source == SOURCE,
        Listing.maakunta == state.maakunta,
        Listing.is_active.is_(True),
        Listing.last_seen_at < cutoff,
    ).all()
    for listing in stale:
        listing.is_active = False
        listing.removed_at = now
    return len(stale)


# ---------------------------------------------------------------------------
# Analysis queue (n8n Analyzer)
# ---------------------------------------------------------------------------


def _listing_brief(listing: Listing) -> dict:
    return {
        "listing_id": listing.id,
        "external_id": listing.external_id,
        "url": listing.url,
        "maakunta": listing.maakunta,
        "kaupunki": listing.kaupunki,
        "osoite": listing.osoite,
        "hinta": listing.hinta,
        "description": listing.description,
        "taloyhtio": listing.taloyhtio,
        "isannoitsija": listing.isannoitsija,
        "build_year": listing.build_year,
        "building_type": listing.building_type,
        "source": listing.source,
        # Only Oikotie listings have a detail page worth fetching. Imported
        # listings carry their full text in `description`.
        "fetch_detail": listing.source == SOURCE,
    }


def _claim(status_col, claimed_col, attempts_col, *, limit: int, now: datetime, order_by) -> list[Listing]:
    cutoff = now - timedelta(minutes=_cfg("LISTING_CLAIM_TIMEOUT_MINUTES"))
    candidates = (
        Listing.query.filter(
            Listing.is_active.is_(True),
            attempts_col < _cfg("LISTING_MAX_ATTEMPTS"),
            or_(
                status_col == "pending",
                (status_col == "processing") & (claimed_col < cutoff),
            ),
        )
        .order_by(*order_by)
        .limit(limit * 2)
        .all()
    )
    claimed: list[Listing] = []
    for listing in candidates:
        if len(claimed) >= limit:
            break
        current_status = getattr(listing, status_col.key)
        result = db.session.execute(
            update(Listing)
            .where(Listing.id == listing.id, status_col == current_status)
            .values(
                {
                    status_col.key: "processing",
                    claimed_col.key: now,
                    # counted at claim time so a listing that crashes n8n before
                    # it can report back still runs out of attempts
                    attempts_col.key: attempts_col + 1,
                }
            )
        )
        if result.rowcount == 1:
            claimed.append(listing)
    db.session.commit()
    for listing in claimed:
        db.session.refresh(listing)
    return claimed


def claim_analysis(*, limit: int, now: datetime | None = None) -> dict:
    now = now or _now()
    limit = max(1, min(int(limit or 20), 100))
    claimed = _claim(
        Listing.analysis_status,
        Listing.analysis_claimed_at,
        Listing.analysis_attempts,
        limit=limit,
        now=now,
        # newest listings first so fresh ones beat the backfill
        order_by=(func.coalesce(Listing.published_at, Listing.first_seen_at).desc(), Listing.id.desc()),
    )
    return {
        "listings": [_listing_brief(l) for l in claimed],
        "remonttityypit": list(REMONTTITYYPIT),
        "talotyypit": list(BUILDING_TYPES),
    }


def _get_listing(listing_id: int) -> Listing:
    listing = db.session.get(Listing, listing_id)
    if listing is None:
        raise ListingServiceError("listing not found")
    return listing


def save_analysis(listing_id: int, payload: dict, *, now: datetime | None = None) -> dict:
    now = now or _now()
    listing = _get_listing(listing_id)
    status = payload.get("status")
    if status not in ("done", "failed"):
        raise ListingServiceError("status must be done or failed")

    listing.analysis_claimed_at = None

    if status == "failed":
        listing.analysis_error = _clean(payload.get("error"), 2000) or "failed"
        listing.analysis_status = (
            "failed" if listing.analysis_attempts >= _cfg("LISTING_MAX_ATTEMPTS") else "pending"
        )
        db.session.commit()
        return {"status": listing.analysis_status, "renovations": 0, "matches": 0}

    renovations = payload.get("renovations") or []
    if not isinstance(renovations, list):
        raise ListingServiceError("renovations must be a list")

    for field, max_len in (
        ("taloyhtio", 255),
        ("tulevat_remontit", 5000),
        ("isannoitsija", 255),
        ("isannoitsija_puh", 50),
        ("isannoitsija_email", 255),
    ):
        value = _clean(payload.get(field), max_len)
        if value:
            setattr(listing, field, value)
    if listing.has_contact and not listing.contact_source:
        listing.contact_source = listing.source
    # the card's own values win; the AI fills what the card did not have
    if listing.build_year is None:
        listing.build_year = _parse_year(payload.get("rakennusvuosi"))
    if not listing.building_type:
        listing.building_type = _parse_building_type(payload.get("talotyyppi"))

    listing.renovations.clear()
    best: dict[str, ListingRenovation] = {}
    for item in renovations:
        if not isinstance(item, dict):
            continue
        rtype = _clean(item.get("remonttityyppi"))
        if rtype not in REMONTTITYYPIT:
            continue
        try:
            confidence = max(0, min(100, int(float(item.get("confidence") or 0))))
        except (TypeError, ValueError):
            confidence = 0
        if rtype in best and best[rtype].confidence >= confidence:
            continue
        best[rtype] = ListingRenovation(
            remonttityyppi=rtype,
            confidence=confidence,
            timing=_clean(item.get("timing"), 100),
            timing_year=_parse_year(item.get("timing"), low=2000),
            evidence=_clean(item.get("evidence"), 2000),
        )
    for renovation in best.values():
        listing.renovations.append(renovation)

    listing.analysis_status = "done"
    listing.analyzed_at = now
    listing.analysis_error = None
    db.session.flush()

    new_matches = match_listing(listing, now=now)
    db.session.commit()
    created = 0
    if new_matches and auto_create_leads():
        created = materialize_pending_matches(profile_ids={m.profile_id for m in new_matches}, now=now)
    return {"status": "done", "renovations": len(best), "matches": len(new_matches), "leads_created": created}


# ---------------------------------------------------------------------------
# Matching (CRM) — profiles are filters over the catalogue
# ---------------------------------------------------------------------------


def _norm(value: str | None) -> str:
    return " ".join(str(value or "").lower().split())


def _listing_passes(profile: SearchProfile, listing: Listing) -> bool:
    """Region, city and building filters. An unknown value on the listing
    (no build year / building type) never excludes it."""
    if listing.maakunta not in profile_regions(profile):
        return False
    cities = {_norm(c) for c in (profile.cities or []) if _norm(c)}
    if cities and _norm(listing.kaupunki) not in cities:
        return False
    if listing.build_year is not None:
        if profile.build_year_min is not None and listing.build_year < profile.build_year_min:
            return False
        if profile.build_year_max is not None and listing.build_year > profile.build_year_max:
            return False
    types = [t for t in (profile.building_types or []) if t]
    if types and listing.building_type and listing.building_type not in types:
        return False
    return True


def _best_renovation(
    profile: SearchProfile, listing: Listing, *, now: datetime | None = None
) -> ListingRenovation | None:
    """The profile's best (highest confidence) wanted renovation on the listing."""
    now = now or _now()
    wanted = set(profile.wanted_types)
    limit_year = None
    if profile.timing_within_years is not None:
        limit_year = now.year + int(profile.timing_within_years)
    best = None
    for renovation in listing.renovations:
        if renovation.remonttityyppi not in wanted:
            continue
        if renovation.confidence < (profile.min_confidence or 0):
            continue
        if limit_year is not None and renovation.timing_year is not None and renovation.timing_year > limit_year:
            continue
        if best is None or renovation.confidence > best.confidence:
            best = renovation
    return best


def _profile_wants(
    profile: SearchProfile, listing: Listing, *, now: datetime | None = None
) -> ListingRenovation | None:
    if not profile.is_active or profile.source != SOURCE:
        return None
    if not _listing_passes(profile, listing):
        return None
    return _best_renovation(profile, listing, now=now)


def _ensure_contact_queued(listing: Listing) -> None:
    if not listing.has_contact and listing.contact_status == "none":
        listing.contact_status = "pending"


def match_listing(listing: Listing, *, now: datetime | None = None) -> list[ListingMatch]:
    """Record a match for every active profile this freshly analysed listing
    satisfies. Leads are created separately (materialize_pending_matches)."""
    if listing.analysis_status != "done":
        return []
    now = now or _now()
    existing: set[int] = set()
    for match in ListingMatch.query.filter_by(listing_id=listing.id).all():
        if (
            match.status == "pending"
            and match.profile is not None
            and _profile_wants(match.profile, listing, now=now) is None
        ):
            # re-analysed / re-imported and no longer what the profile wants
            # (e.g. the renovation type changed): not a target any more
            db.session.delete(match)
            continue
        existing.add(match.profile_id)
    if not listing.is_active or not listing.renovations:
        db.session.flush()
        return []
    created: list[ListingMatch] = []
    for profile in _active_profiles():
        if profile.id in existing:
            continue
        renovation = _profile_wants(profile, listing, now=now)
        if renovation is None:
            continue
        match = ListingMatch(
            organization_id=profile.organization_id,
            profile_id=profile.id,
            listing_id=listing.id,
            status="pending",
            confidence=renovation.confidence,
            created_at=now,
            released_at=_release_time(now),
        )
        db.session.add(match)
        created.append(match)
    if created:
        _ensure_contact_queued(listing)
    db.session.flush()
    return created


def wanted_listings(profile: SearchProfile, *, now: datetime | None = None) -> dict[int, tuple[Listing, int]]:
    """Catalogue listings the profile's filter accepts right now:
    {listing_id: (listing, confidence)}. Only listings from the last
    LISTING_MATCH_HISTORY_DAYS, so a new profile is not flooded with old ones.
    Works for an unsaved profile too (the settings page preview)."""
    now = now or _now()
    regions = profile_regions(profile)
    if not regions or (profile.source or SOURCE) != SOURCE:
        return {}
    since = now - timedelta(days=_cfg("LISTING_MATCH_HISTORY_DAYS"))
    listings = (
        Listing.query.join(ListingRenovation, ListingRenovation.listing_id == Listing.id)
        .filter(
            Listing.is_active.is_(True),
            Listing.analysis_status == "done",
            Listing.maakunta.in_(regions),
            ListingRenovation.remonttityyppi.in_(profile.wanted_types),
            ListingRenovation.confidence >= (profile.min_confidence or 0),
            func.coalesce(Listing.published_at, Listing.first_seen_at) >= since,
        )
        .distinct()
        .all()
    )
    wanted: dict[int, tuple[Listing, int]] = {}
    for listing in listings:
        if not _listing_passes(profile, listing):
            continue
        renovation = _best_renovation(profile, listing, now=now)
        if renovation is not None:
            wanted[listing.id] = (listing, renovation.confidence)
    return wanted


def rematch_profile(profile: SearchProfile, *, now: datetime | None = None) -> int:
    """(Re)build a profile's pending matches from the catalogue. Runs when a
    profile is created, activated or its filter changes. Matches that already
    became leads are kept."""
    now = now or _now()
    pending = ListingMatch.query.filter_by(profile_id=profile.id, status="pending").all()

    if not profile.is_active or profile.source != SOURCE:
        for match in pending:
            db.session.delete(match)
        profile.needs_rematch = False
        db.session.commit()
        return 0

    wanted = wanted_listings(profile, now=now)

    for match in pending:
        if match.listing_id not in wanted:
            db.session.delete(match)
    have = {
        m.listing_id
        for m in ListingMatch.query.filter_by(profile_id=profile.id).all()
    }
    added = 0
    for listing_id, (listing, conf) in wanted.items():
        if listing_id in have:
            continue
        db.session.add(
            ListingMatch(
                organization_id=profile.organization_id,
                profile_id=profile.id,
                listing_id=listing_id,
                status="pending",
                confidence=conf,
                created_at=now,
                released_at=_release_time(now),
            )
        )
        _ensure_contact_queued(listing)
        added += 1

    profile.needs_rematch = False
    db.session.commit()
    return added


def rematch_profiles(*, now: datetime | None = None) -> int:
    total = 0
    for profile in SearchProfile.query.filter(SearchProfile.needs_rematch.is_(True)).all():
        total += rematch_profile(profile, now=now)
    return total


def lead_source(listing: Listing) -> str:
    """Lead.source for a catalogue lead: Oikotie listings keep "oikotie", every
    other listing source is an import."""
    return SOURCE if listing.source == SOURCE else IMPORT_SOURCE


def _manager_title(listing: Listing) -> str:
    """'Isännöitsijä: Retta Isännöinti Oy, Hannu Räsänen' (max 150 chars)."""
    parts = [p for p in (listing.isannoitsija, listing.isannoitsija_henkilo) if p]
    return ("Isännöitsijä: " + ", ".join(parts))[:150] if parts else "Isännöitsijä"


def _lead_payload(listing: Listing, profile: SearchProfile) -> dict:
    manager = listing.isannoitsija or ""
    source = lead_source(listing)
    renovation = _best_renovation(profile, listing)
    rtype = renovation.remonttityyppi if renovation else profile.remonttityyppi
    building = ", ".join(
        p for p in (listing.building_type, listing.build_year and f"rakennettu {listing.build_year}") if p
    )
    notes = "\n".join(
        part
        for part in (
            listing.taloyhtio and f"Taloyhtiö: {listing.taloyhtio}",
            listing.ytunnus and f"Taloyhtiön Y-tunnus: {listing.ytunnus}",
            listing.isannoitsija_henkilo and f"Yhteyshenkilö: {listing.isannoitsija_henkilo}",
            f"Kohde: {', '.join(p for p in (listing.osoite, listing.kaupunki) if p)}",
            building and f"Rakennus: {building}",
            listing.tulevat_remontit and f"Tulevat remontit: {listing.tulevat_remontit}",
            renovation and renovation.evidence and f"Osuma ({rtype}): {renovation.evidence}",
            listing.url and f"{'Oikotie' if source == SOURCE else 'Lähde'}: {listing.url}",
        )
        if part
    )
    website = listing.isannoitsija_website if (listing.isannoitsija_website or "").startswith("http") else None
    return {
        "source": source,
        "source_ref": listing.source_ref,
        # The lead is the housing company's renovation: it is named after the
        # taloyhtiö (one manager serves dozens of them) and the manager is the
        # contact, shown under the name.
        "company": listing.taloyhtio or manager or None,
        "title": _manager_title(listing) if manager else None,
        "phone": listing.isannoitsija_puh or None,
        "email": _valid_email_or_none(listing.isannoitsija_email),
        "website": website,
        "notes": notes,
        "tags": [t[:50] for t in (rtype, source, listing.maakunta) if t],
        "gdpr_legal_basis": "legitimate_interest",
        "metadata": _lead_metadata(listing, profile, renovation),
    }


def _lead_metadata(listing: Listing, profile: SearchProfile | None, renovation) -> dict:
    meta = {
        "isannoitsija_yritys": listing.isannoitsija or "",
        "isannoitsija_henkilo": listing.isannoitsija_henkilo or "",
        "taloyhtio_ytunnus": listing.ytunnus or "",
        "isannoitsija_puh": listing.isannoitsija_puh or "",
        "isannoitsija_email": listing.isannoitsija_email or "",
        "taloyhtio": listing.taloyhtio or "",
        "tulevat_remontit": listing.tulevat_remontit or "",
        ("oikotie_url" if listing.source == SOURCE else "lahde_url"): listing.url or "",
        "kaupunki": listing.kaupunki or "",
        "osoite": listing.osoite or "",
        "hinta": listing.hinta or "",
        "maakunta": listing.maakunta,
        "listing_id": listing.id,
        "yhteystieto_lahde": listing.contact_source or "",
        "rakennusvuosi": listing.build_year or "",
        "talotyyppi": listing.building_type or "",
    }
    if profile is not None:
        meta["remonttityyppi"] = renovation.remonttityyppi if renovation is not None else profile.remonttityyppi
        meta["search_profile_id"] = profile.id
    if renovation is not None:
        meta["ai_confidence"] = renovation.confidence
        meta["remontin_ajankohta"] = renovation.timing or ""
    return meta


def _create_lead(listing: Listing, profile: SearchProfile):
    from app.api.services import upsert_lead
    from app.streams.services import LeadRoutingService

    # A lead here is "this housing company's renovation", not "this property
    # manager": one manager email serves dozens of housing companies, so the
    # catalogue never merges leads by email (only by the Oikotie reference).
    lead, action = upsert_lead(
        profile.organization_id, _lead_payload(listing, profile), match_by_email=False
    )
    if action == "created":
        lead.region = listing.maakunta  # the housing company's region, not an org default
        settings = LeadRoutingService.get_settings(profile.organization_id)
        LeadRoutingService.apply_to_lead(lead, settings, new=True)
        if not lead.stage_id:
            fallback = LeadRoutingService.get_fallback_stage(profile.organization_id)
            if fallback:
                lead.stage_id = fallback.id
    return lead, action


def _same_housing_company_lead(match: ListingMatch, listing: Listing):
    """An existing lead of this profile for the same housing company (another
    apartment of the same taloyhtiö is on sale), or None."""
    from app.leads.models import Lead

    name = _norm(listing.taloyhtio)
    if not name:
        return None
    query = (
        db.session.query(ListingMatch.lead_id)
        .join(Listing, Listing.id == ListingMatch.listing_id)
        .filter(
            ListingMatch.profile_id == match.profile_id,
            ListingMatch.id != match.id,
            ListingMatch.lead_id.isnot(None),
            func.lower(func.trim(Listing.taloyhtio)) == name,
        )
        .order_by(ListingMatch.lead_created_at.asc())
    )
    city = _norm(listing.kaupunki)
    if city:
        query = query.filter(func.lower(func.trim(Listing.kaupunki)) == city)
    for (lead_id,) in query.all():
        lead = db.session.get(Lead, lead_id)
        if lead is not None:
            return lead
    return None


def _valid_email_or_none(value) -> str | None:
    """Scraped and imported contact emails are free text ("info@firma",
    "matti(at)x.fi"). A value the lead API would reject must not sink the
    whole target: the lead is created without it (the raw text stays in the
    lead's property-manager details)."""
    from app.core.security import normalize_email, validate_email

    email = normalize_email(value or "")
    return email if email and validate_email(email) else None


def _release_time(now: datetime) -> datetime | None:
    """released_at for a new match: at once, or None (reserve) when customers
    release targets themselves with "Hae kohteita"."""
    from flask import current_app

    return None if current_app.config.get("TARGET_FETCH_ENABLED") else now


def _merge_into_lead(match: ListingMatch, listing: Listing, lead, now: datetime) -> None:
    from app.leads.services import LeadService

    match.status = "merged"
    match.lead_id = lead.id
    match.lead_created_at = now
    where = ", ".join(p for p in (listing.osoite, listing.kaupunki) if p)
    try:
        LeadService.log_activity(
            lead.id,
            None,
            "updated",
            content=(
                "Samasta taloyhtiöstä tuli myyntiin toinen kohde"
                + (f" ({where})" if where else "")
                + (f": {listing.url}" if listing.url else "")
            ),
        )
    except Exception:  # an activity note must never block matching
        logger.exception("Could not log merged listing %s on lead %s", listing.id, lead.id)


def merge_into_existing_leads(
    *, profile_ids: set[int] | None = None, now: datetime | None = None
) -> int:
    """Another apartment of a housing company that already has a lead joins
    that lead instead of waiting as a separate target. Creates no leads."""
    now = now or _now()
    query = ListingMatch.query.join(Listing, Listing.id == ListingMatch.listing_id).filter(
        ListingMatch.status == "pending",
        Listing.is_active.is_(True),
        Listing.taloyhtio.isnot(None),
    )
    if profile_ids:
        query = query.filter(ListingMatch.profile_id.in_(profile_ids))
    merged = 0
    for match in query.all():
        same = _same_housing_company_lead(match, match.listing)
        if same is not None:
            _merge_into_lead(match, match.listing, same, now)
            merged += 1
    if merged:
        db.session.commit()
    return merged


def materialize_match(match: ListingMatch, *, now: datetime | None = None) -> tuple[str, object | None]:
    """Move one match to a lead and commit. Returns (outcome, lead):
    "created" / "updated" (the lead already existed), "merged" (joined the
    housing company's existing lead) or "failed" (the CRM rejected it)."""
    from app.api.services import ApiServiceError

    now = now or _now()
    match_id, listing, profile = match.id, match.listing, match.profile
    same = _same_housing_company_lead(match, listing)
    if same is not None:
        _merge_into_lead(match, listing, same, now)
        db.session.commit()
        return "merged", same
    try:
        lead, action = _create_lead(listing, profile)
        db.session.flush()
    except ApiServiceError as exc:
        db.session.rollback()
        logger.warning("Listing match %s -> lead failed: %s", match_id, exc.message)
        match = db.session.get(ListingMatch, match_id)
        match.status = "lead_created"  # do not retry a payload the CRM rejects
        match.lead_created_at = now
        db.session.commit()
        return "failed", None
    match.status = "lead_created"
    match.lead_id = lead.id
    match.lead_created_at = now
    if action == "created":
        profile.total_leads_sent = (profile.total_leads_sent or 0) + 1
    profile.last_run_at = now
    db.session.commit()
    return action, lead


def materialize_pending_matches(
    *, profile_ids: set[int] | None = None, now: datetime | None = None
) -> int:
    """Turn pending matches into leads, newest listings first, never more than
    a profile's daily_lead_limit per UTC day (0 = unlimited). Only runs
    automatically when LISTING_AUTO_CREATE_LEADS is on; by default users move
    matches to leads themselves on the Kohteet page."""
    now = now or _now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    query = (
        db.session.query(ListingMatch.profile_id)
        .filter(ListingMatch.status == "pending")
        .distinct()
    )
    if profile_ids:
        query = query.filter(ListingMatch.profile_id.in_(profile_ids))
    target_ids = [row[0] for row in query.all()]

    created_total = 0
    for profile_id in target_ids:
        profile = db.session.get(SearchProfile, profile_id)
        if profile is None or not profile.is_active:
            continue
        # Another apartment of a housing company that already has a lead joins
        # that lead. This creates nothing, so it runs before (and regardless
        # of) the daily limit.
        merge_into_existing_leads(profile_ids={profile_id}, now=now)

        limit = profile.daily_lead_limit or 0
        if limit > 0:
            today = ListingMatch.query.filter(
                ListingMatch.profile_id == profile_id,
                ListingMatch.status == "lead_created",
                ListingMatch.lead_created_at >= day_start,
            ).count()
            quota = limit - today
            if quota <= 0:
                continue
        else:
            quota = None

        pending = (
            ListingMatch.query.join(Listing, Listing.id == ListingMatch.listing_id)
            .filter(
                ListingMatch.profile_id == profile_id,
                ListingMatch.status == "pending",
                Listing.is_active.is_(True),
            )
            .order_by(func.coalesce(Listing.published_at, Listing.first_seen_at).desc(), Listing.id.desc())
        )
        if quota is not None:
            pending = pending.limit(quota)

        for match_id in [m.id for m in pending.all()]:
            # a merge creates no new lead and uses no daily quota
            outcome, _lead = materialize_match(db.session.get(ListingMatch, match_id), now=now)
            if outcome == "created":
                created_total += 1
    return created_total


# ---------------------------------------------------------------------------
# Contact enrichment queue (n8n Contact Enricher)
# ---------------------------------------------------------------------------


def claim_contacts(*, limit: int, now: datetime | None = None) -> dict:
    now = now or _now()
    limit = max(1, min(int(limit or 10), 50))
    claimed = _claim(
        Listing.contact_status,
        Listing.contact_claimed_at,
        Listing.contact_attempts,
        limit=limit,
        now=now,
        order_by=(Listing.id.desc(),),
    )
    return {"listings": [_listing_brief(l) for l in claimed]}


def save_contacts(listing_id: int, payload: dict, *, now: datetime | None = None) -> dict:
    from app.api.services import ApiServiceError, upsert_lead
    from app.leads.models import Lead

    now = now or _now()
    listing = _get_listing(listing_id)
    status = payload.get("status")
    if status not in ("done", "not_found", "failed"):
        raise ListingServiceError("status must be done, not_found or failed")

    listing.contact_claimed_at = None
    if status == "failed":
        listing.contact_status = (
            "not_found" if listing.contact_attempts >= _cfg("LISTING_MAX_ATTEMPTS") else "pending"
        )
        db.session.commit()
        return {"status": listing.contact_status, "leads_updated": 0}

    fields = {
        "isannoitsija": _clean(payload.get("isannoitsija"), 255),
        "isannoitsija_puh": _clean(payload.get("phone"), 50),
        "isannoitsija_email": _clean(payload.get("email"), 255),
        "isannoitsija_website": _clean(payload.get("website"), 500),
    }
    if fields["isannoitsija_email"] and "@" not in fields["isannoitsija_email"]:
        fields["isannoitsija_email"] = None
    for key, value in fields.items():
        if value and not getattr(listing, key):
            setattr(listing, key, value)
    if (fields["isannoitsija_puh"] or fields["isannoitsija_email"]) and not listing.contact_source:
        listing.contact_source = _clean(payload.get("source"), 50) or "fallback"
    listing.contact_status = "done" if listing.has_contact else "not_found"
    db.session.commit()

    updated = 0
    matches = ListingMatch.query.filter(
        ListingMatch.listing_id == listing.id, ListingMatch.lead_id.isnot(None)
    ).all()
    for match in matches:
        lead = db.session.get(Lead, match.lead_id)
        if not lead:
            continue
        if not lead.source_ref:
            continue
        # A merged match points at a lead made from another apartment of the
        # same housing company: give it the contacts, keep its own property data.
        patch = {"source": lead.source or SOURCE, "source_ref": lead.source_ref}
        if lead.source_ref == listing.source_ref:
            patch["metadata"] = _lead_metadata(listing, None, None)
        else:
            patch["metadata"] = {
                "isannoitsija_yritys": listing.isannoitsija or "",
                "isannoitsija_henkilo": listing.isannoitsija_henkilo or "",
                "isannoitsija_puh": listing.isannoitsija_puh or "",
                "isannoitsija_email": listing.isannoitsija_email or "",
                "yhteystieto_lahde": listing.contact_source or "",
            }
        if not lead.phone and listing.isannoitsija_puh:
            patch["phone"] = listing.isannoitsija_puh
        if not lead.email and listing.isannoitsija_email:
            patch["email"] = listing.isannoitsija_email
        if not lead.company and (listing.taloyhtio or listing.isannoitsija):
            patch["company"] = listing.taloyhtio or listing.isannoitsija
        if listing.isannoitsija and (not lead.title or lead.title.startswith("Isännöitsijä")):
            patch["title"] = _manager_title(listing)
        if not lead.website and (listing.isannoitsija_website or "").startswith("http"):
            patch["website"] = listing.isannoitsija_website
        try:
            upsert_lead(match.organization_id, patch, match_by_email=False)
            db.session.commit()
            updated += 1
        except ApiServiceError as exc:
            db.session.rollback()
            logger.warning("Contact update for lead %s failed: %s", match.lead_id, exc.message)
    return {"status": listing.contact_status, "leads_updated": updated}


# ---------------------------------------------------------------------------
# Profile UI helpers
# ---------------------------------------------------------------------------


def profile_match_stats(profile: SearchProfile, *, include_reserve: bool = True) -> dict:
    """Match counts of a profile. include_reserve=False (what the customer
    sees): pending matches still in the "Hae kohteita" reserve are left out,
    so the settings page never shows more than the Kohteet page."""
    criteria = [ListingMatch.profile_id == profile.id]
    try:
        reserve_on = bool(current_app.config.get("TARGET_FETCH_ENABLED"))
    except RuntimeError:
        reserve_on = False
    if reserve_on and not include_reserve:
        criteria.append(
            or_(
                ListingMatch.status != "pending",
                and_(ListingMatch.released_at.isnot(None), ListingMatch.released_at <= _now()),
            )
        )
    rows = (
        db.session.query(ListingMatch.status, func.count(ListingMatch.id))
        .filter(*criteria)
        .group_by(ListingMatch.status)
        .all()
    )
    counts = {status: count for status, count in rows}
    recent = (
        ListingMatch.query.filter(*criteria)
        .order_by(ListingMatch.created_at.desc(), ListingMatch.id.desc())
        .limit(10)
        .all()
    )
    from app.listings.target_services import _visible_match_ids

    # the same rule as the Kohteet page (one target per listing, not already a
    # lead or hidden through another profile, active listing)
    available = _visible_match_ids(
        profile.organization_id, "pending", profile_id=profile.id, include_reserve=include_reserve
    ).count()
    return {
        "total": sum(counts.values()),
        "lead_created": counts.get("lead_created", 0),
        "pending": available,
        "merged": counts.get("merged", 0),
        "recent": recent,
    }


def preview_profile(profile: SearchProfile, *, now: datetime | None = None) -> dict:
    """How many catalogue listings an (unsaved) filter would match right now,
    leaving out what the organization already moved to leads or hid."""
    wanted = wanted_listings(profile, now=now)
    if profile.organization_id and wanted:
        handled = {
            listing_id
            for (listing_id,) in db.session.query(ListingMatch.listing_id).filter(
                ListingMatch.organization_id == profile.organization_id,
                ListingMatch.status.in_(("lead_created", "merged", "dismissed")),
            )
        }
        wanted = {k: v for k, v in wanted.items() if k not in handled}
    sample = sorted(wanted.values(), key=lambda pair: (-pair[1], -pair[0].id))[:5]
    return {
        "count": len(wanted),
        "history_days": _cfg("LISTING_MATCH_HISTORY_DAYS"),
        "sample": [
            {
                "osoite": listing.osoite or listing.external_id,
                "kaupunki": listing.kaupunki or listing.maakunta,
                "confidence": confidence,
            }
            for listing, confidence in sample
        ],
    }


def catalogue_cities(regions: list[str] | None = None, *, limit: int = 400) -> list[str]:
    """Distinct cities seen in the catalogue (suggestions for the profile form)."""
    query = db.session.query(Listing.kaupunki).filter(
        Listing.kaupunki.isnot(None), Listing.kaupunki != "", Listing.is_active.is_(True)
    )
    if regions:
        query = query.filter(Listing.maakunta.in_(regions))
    return [row[0] for row in query.distinct().order_by(Listing.kaupunki.asc()).limit(limit).all()]


def catalogue_stats() -> dict:
    total = Listing.query.filter(Listing.is_active.is_(True)).count()
    analysed = Listing.query.filter(Listing.is_active.is_(True), Listing.analysis_status == "done").count()
    return {"active": total, "analysed": analysed, "pending_analysis": total - analysed}


# ---------------------------------------------------------------------------
# Retention + health (CRM scheduler, superadmin view)
# ---------------------------------------------------------------------------


def purge_removed_listings(*, now: datetime | None = None, batch_size: int = 500) -> int:
    """Delete listings that left Oikotie more than LISTING_RETENTION_DAYS ago,
    with their renovations and matches (they hold property-manager contact
    data, which is not kept longer than needed). Leads already created stay in
    the customer's CRM under that organization's own retention rules."""
    now = now or _now()
    days = _cfg("LISTING_RETENTION_DAYS")
    if days <= 0:
        return 0
    cutoff = now - timedelta(days=days)
    total = 0
    while True:
        ids = [
            row[0]
            for row in db.session.query(Listing.id)
            .filter(Listing.is_active.is_(False), Listing.removed_at.isnot(None), Listing.removed_at < cutoff)
            .limit(batch_size)
            .all()
        ]
        if not ids:
            break
        ListingMatch.query.filter(ListingMatch.listing_id.in_(ids)).delete(synchronize_session=False)
        ListingRenovation.query.filter(ListingRenovation.listing_id.in_(ids)).delete(synchronize_session=False)
        Listing.query.filter(Listing.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        total += len(ids)
    return total


def catalogue_health(*, now: datetime | None = None) -> dict:
    """Everything a superadmin needs to see whether lead acquisition is alive:
    per-region crawl state, the queues, and a list of `problems`."""
    now = now or _now()
    stale_after = timedelta(hours=_cfg("LISTING_STALE_HOURS"))

    states = {s.maakunta: s for s in CrawlState.query.filter_by(source=SOURCE).all()}
    counts = dict(
        db.session.query(Listing.maakunta, func.count(Listing.id))
        .filter(Listing.is_active.is_(True))
        .group_by(Listing.maakunta)
        .all()
    )
    regions = []
    for name in FINNISH_REGIONS:
        state = states.get(name)
        regions.append(
            {
                "maakunta": name,
                "listings": counts.get(name, 0),
                "last_incremental_at": _aware(state.last_incremental_at) if state else None,
                "full_completed_at": _aware(state.full_completed_at) if state else None,
                "full_in_progress": bool(state and _full_in_progress(state)),
                "full_offset": state.full_offset if state else 0,
                "last_error": state.last_error if state else None,
                "last_error_at": _aware(state.last_error_at) if state else None,
            }
        )

    def _count(*criteria) -> int:
        return Listing.query.filter(Listing.is_active.is_(True), *criteria).count()

    analysis = {
        "pending": _count(Listing.analysis_status == "pending"),
        "processing": _count(Listing.analysis_status == "processing"),
        "done": _count(Listing.analysis_status == "done"),
        "failed": _count(Listing.analysis_status == "failed"),
    }
    contacts = {
        "pending": _count(Listing.contact_status == "pending"),
        "processing": _count(Listing.contact_status == "processing"),
        "done": _count(Listing.contact_status == "done"),
        "not_found": _count(Listing.contact_status == "not_found"),
    }
    last_crawl = max((r["last_incremental_at"] for r in regions if r["last_incremental_at"]), default=None)
    last_analysis = _aware(db.session.query(func.max(Listing.analyzed_at)).scalar())
    oldest_pending = _aware(
        db.session.query(func.min(Listing.first_seen_at))
        .filter(Listing.is_active.is_(True), Listing.analysis_status == "pending")
        .scalar()
    )
    matches = dict(
        db.session.query(ListingMatch.status, func.count(ListingMatch.id)).group_by(ListingMatch.status).all()
    )
    # pending matches still waiting for the customer's "Hae kohteita"
    reserve = (
        ListingMatch.query.filter(ListingMatch.status == "pending", ListingMatch.released_at.is_(None)).count()
    )
    recent_failures = (
        Listing.query.filter(Listing.analysis_status == "failed", Listing.is_active.is_(True))
        .order_by(Listing.id.desc())
        .limit(10)
        .all()
    )

    problems: list[str] = []
    hours = _cfg("LISTING_STALE_HOURS")
    if states and (last_crawl is None or now - last_crawl > stale_after):
        problems.append(
            f"Keruu (Crawler) ei ole tuonut yhtään sivua yli {hours} tuntiin"
            + (f" (viimeksi {_helsinki_label(last_crawl)})." if last_crawl else ".")
        )
    errored = [r["maakunta"] for r in regions if r["last_error"]]
    if errored:
        problems.append("Keruuvirhe maakunnissa: " + ", ".join(errored) + ".")
    if analysis["pending"] and oldest_pending and now - oldest_pending > stale_after:
        if last_analysis is None or now - last_analysis > stale_after:
            problems.append(
                f"Analyysi (Analyzer) ei ole käsitellyt kohteita yli {hours} tuntiin, "
                f"jonossa {analysis['pending']} kohdetta."
            )
    total_active = sum(counts.values())
    if total_active and analysis["failed"] > max(50, total_active // 10):
        problems.append(f"Analyysi on epäonnistunut {analysis['failed']} kohteelle.")

    return {
        "checked_at": now,
        "regions": regions,
        "analysis": analysis,
        "contacts": contacts,
        "matches": {
            "pending": matches.get("pending", 0),
            "reserve": reserve,
            "released": max(0, matches.get("pending", 0) - reserve),
            "lead_created": matches.get("lead_created", 0),
            "merged": matches.get("merged", 0),
        },
        "total_active": total_active,
        "last_crawl_at": last_crawl,
        "last_analysis_at": last_analysis,
        "recent_failures": recent_failures,
        "active_profiles": len(_active_profiles()),
        "problems": problems,
        "started": bool(states),
    }


def _helsinki_label(value: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo

        return _aware(value).astimezone(ZoneInfo("Europe/Helsinki")).strftime("%d.%m.%Y %H.%M")
    except Exception:
        return value.strftime("%d.%m.%Y %H:%M UTC")


def alert_catalogue_problems(*, now: datetime | None = None) -> int:
    """Email superadmins when the catalogue pipeline looks broken. At most one
    alert per LISTING_ALERT_INTERVAL_HOURS (tracked in the audit log)."""
    from app.core.audit import log_audit
    from app.email.services import EmailService
    from app.users.models import AuditLog, User

    now = now or _now()
    health = catalogue_health(now=now)
    if not health["problems"]:
        return 0
    since = now - timedelta(hours=_cfg("LISTING_ALERT_INTERVAL_HOURS"))
    recent = (
        AuditLog.query.filter(AuditLog.action == "catalogue_alert_sent", AuditLog.created_at >= since).first()
    )
    if recent is not None:
        return 0
    sent = 0
    for admin in User.query.filter_by(role="superadmin", is_active=True).all():
        EmailService.send_catalogue_alert(to_email=admin.email, problems=health["problems"])
        sent += 1
    log_audit("catalogue_alert_sent", metadata={"problems": health["problems"], "recipients": sent})
    db.session.commit()
    return sent
