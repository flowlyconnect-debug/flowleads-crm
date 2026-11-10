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
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import func, or_, update

from app.extensions import db
from app.listings.models import CrawlState, Listing, ListingMatch, ListingRenovation
from app.search.constants import ALL_FINLAND_REGION, FINNISH_REGIONS, REMONTTITYYPIT
from app.search.models import SearchProfile

logger = logging.getLogger(__name__)

SOURCE = "oikotie"
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
                **{k: r[k] for k in ("url", "kaupunki", "osoite", "hinta", "alue", "description", "published_at")},
            )
            db.session.add(listing)
            existing[r["external_id"]] = listing
            created += 1
            continue

        listing.last_seen_at = now
        if not listing.is_active:
            listing.is_active = True
            listing.removed_at = None
        for key in ("url", "kaupunki", "osoite", "hinta", "alue", "description", "published_at"):
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
    return {"listings": [_listing_brief(l) for l in claimed], "remonttityypit": list(REMONTTITYYPIT)}


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
        listing.contact_source = "oikotie"

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
    if new_matches:
        created = materialize_pending_matches(profile_ids={m.profile_id for m in new_matches}, now=now)
    return {"status": "done", "renovations": len(best), "matches": len(new_matches), "leads_created": created}


# ---------------------------------------------------------------------------
# Matching (CRM) — profiles are filters over the catalogue
# ---------------------------------------------------------------------------


def _profile_wants(profile: SearchProfile, listing: Listing) -> ListingRenovation | None:
    if not profile.is_active or profile.source != SOURCE:
        return None
    if listing.maakunta not in profile_regions(profile):
        return None
    for renovation in listing.renovations:
        if renovation.remonttityyppi == profile.remonttityyppi and renovation.confidence >= (
            profile.min_confidence or 0
        ):
            return renovation
    return None


def _ensure_contact_queued(listing: Listing) -> None:
    if not listing.has_contact and listing.contact_status == "none":
        listing.contact_status = "pending"


def match_listing(listing: Listing, *, now: datetime | None = None) -> list[ListingMatch]:
    """Record a match for every active profile this freshly analysed listing
    satisfies. Leads are created separately (materialize_pending_matches)."""
    if not listing.is_active or listing.analysis_status != "done" or not listing.renovations:
        return []
    now = now or _now()
    existing = {
        m.profile_id for m in ListingMatch.query.filter_by(listing_id=listing.id).all()
    }
    created: list[ListingMatch] = []
    for profile in _active_profiles():
        if profile.id in existing:
            continue
        renovation = _profile_wants(profile, listing)
        if renovation is None:
            continue
        match = ListingMatch(
            organization_id=profile.organization_id,
            profile_id=profile.id,
            listing_id=listing.id,
            status="pending",
            confidence=renovation.confidence,
            created_at=now,
        )
        db.session.add(match)
        created.append(match)
    if created:
        _ensure_contact_queued(listing)
    db.session.flush()
    return created


def rematch_profile(profile: SearchProfile, *, now: datetime | None = None) -> int:
    """(Re)build a profile's pending matches from the catalogue. Runs when a
    profile is created or its filter changes. Only listings seen in the last
    LISTING_MATCH_HISTORY_DAYS are offered, so a new profile is not flooded
    with years-old listings; matches that already became leads are kept."""
    now = now or _now()
    pending = ListingMatch.query.filter_by(profile_id=profile.id, status="pending").all()

    if not profile.is_active or profile.source != SOURCE:
        for match in pending:
            db.session.delete(match)
        profile.needs_rematch = False
        db.session.commit()
        return 0

    regions = profile_regions(profile)
    since = now - timedelta(days=_cfg("LISTING_MATCH_HISTORY_DAYS"))
    rows = (
        db.session.query(Listing, ListingRenovation.confidence)
        .join(ListingRenovation, ListingRenovation.listing_id == Listing.id)
        .filter(
            Listing.source == SOURCE,
            Listing.is_active.is_(True),
            Listing.analysis_status == "done",
            Listing.maakunta.in_(regions or ["__none__"]),
            ListingRenovation.remonttityyppi == profile.remonttityyppi,
            ListingRenovation.confidence >= (profile.min_confidence or 0),
            func.coalesce(Listing.published_at, Listing.first_seen_at) >= since,
        )
        .all()
    )
    wanted = {listing.id: (listing, conf) for listing, conf in rows}

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


def _lead_payload(listing: Listing, profile: SearchProfile) -> dict:
    manager = listing.isannoitsija or ""
    renovation = next(
        (r for r in listing.renovations if r.remonttityyppi == profile.remonttityyppi), None
    )
    notes = "\n".join(
        part
        for part in (
            listing.taloyhtio and f"Taloyhtiö: {listing.taloyhtio}",
            f"Kohde: {', '.join(p for p in (listing.osoite, listing.kaupunki) if p)}",
            listing.tulevat_remontit and f"Tulevat remontit: {listing.tulevat_remontit}",
            renovation and renovation.evidence and f"Osuma ({profile.remonttityyppi}): {renovation.evidence}",
            listing.url and f"Oikotie: {listing.url}",
        )
        if part
    )
    website = listing.isannoitsija_website if (listing.isannoitsija_website or "").startswith("http") else None
    return {
        "source": SOURCE,
        "source_ref": listing.source_ref,
        "company": manager or listing.taloyhtio or None,
        "title": "Isännöitsijä" if manager else None,
        "phone": listing.isannoitsija_puh or None,
        "email": listing.isannoitsija_email or None,
        "website": website,
        "notes": notes,
        "tags": [t[:50] for t in (profile.remonttityyppi, SOURCE, listing.maakunta) if t],
        "gdpr_legal_basis": "legitimate_interest",
        "metadata": _lead_metadata(listing, profile, renovation),
    }


def _lead_metadata(listing: Listing, profile: SearchProfile | None, renovation) -> dict:
    meta = {
        "isannoitsija_yritys": listing.isannoitsija or "",
        "isannoitsija_puh": listing.isannoitsija_puh or "",
        "isannoitsija_email": listing.isannoitsija_email or "",
        "taloyhtio": listing.taloyhtio or "",
        "tulevat_remontit": listing.tulevat_remontit or "",
        "oikotie_url": listing.url or "",
        "kaupunki": listing.kaupunki or "",
        "osoite": listing.osoite or "",
        "hinta": listing.hinta or "",
        "maakunta": listing.maakunta,
        "listing_id": listing.id,
        "yhteystieto_lahde": listing.contact_source or "",
    }
    if profile is not None:
        meta["remonttityyppi"] = profile.remonttityyppi
        meta["search_profile_id"] = profile.id
    if renovation is not None:
        meta["ai_confidence"] = renovation.confidence
        meta["remontin_ajankohta"] = renovation.timing or ""
    return meta


def _create_lead(listing: Listing, profile: SearchProfile):
    from app.api.services import upsert_lead
    from app.streams.services import LeadRoutingService

    lead, action = upsert_lead(profile.organization_id, _lead_payload(listing, profile))
    if action == "created":
        settings = LeadRoutingService.get_settings(profile.organization_id)
        LeadRoutingService.apply_to_lead(lead, settings)
        if not lead.stage_id:
            fallback = LeadRoutingService.get_fallback_stage(profile.organization_id)
            if fallback:
                lead.stage_id = fallback.id
    return lead, action


def materialize_pending_matches(
    *, profile_ids: set[int] | None = None, now: datetime | None = None
) -> int:
    """Turn pending matches into leads, newest listings first, never more than
    a profile's daily_lead_limit per UTC day (0 = unlimited)."""
    from app.api.services import ApiServiceError

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

        for match in pending.all():
            listing = match.listing
            try:
                lead, action = _create_lead(listing, profile)
                db.session.flush()
            except ApiServiceError as exc:
                db.session.rollback()
                logger.warning("Listing %s -> lead failed for profile %s: %s", listing.id, profile_id, exc.message)
                match = db.session.get(ListingMatch, match.id)
                match.status = "lead_created"  # do not retry a payload the CRM rejects
                match.lead_created_at = now
                db.session.commit()
                continue
            match.status = "lead_created"
            match.lead_id = lead.id
            match.lead_created_at = now
            if action == "created":
                profile.total_leads_sent = (profile.total_leads_sent or 0) + 1
                created_total += 1
            profile.last_run_at = now
            db.session.commit()
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
        patch = {"source": SOURCE, "source_ref": listing.source_ref, "metadata": _lead_metadata(listing, None, None)}
        if not lead.phone and listing.isannoitsija_puh:
            patch["phone"] = listing.isannoitsija_puh
        if not lead.email and listing.isannoitsija_email:
            patch["email"] = listing.isannoitsija_email
        if listing.isannoitsija and (not lead.company or lead.company == listing.taloyhtio):
            patch["company"] = listing.isannoitsija
            patch["title"] = "Isännöitsijä"
        if not lead.website and (listing.isannoitsija_website or "").startswith("http"):
            patch["website"] = listing.isannoitsija_website
        try:
            upsert_lead(match.organization_id, patch)
            db.session.commit()
            updated += 1
        except ApiServiceError as exc:
            db.session.rollback()
            logger.warning("Contact update for lead %s failed: %s", match.lead_id, exc.message)
    return {"status": listing.contact_status, "leads_updated": updated}


# ---------------------------------------------------------------------------
# Profile UI helpers
# ---------------------------------------------------------------------------


def profile_match_stats(profile: SearchProfile) -> dict:
    rows = (
        db.session.query(ListingMatch.status, func.count(ListingMatch.id))
        .filter(ListingMatch.profile_id == profile.id)
        .group_by(ListingMatch.status)
        .all()
    )
    counts = {status: count for status, count in rows}
    recent = (
        ListingMatch.query.filter_by(profile_id=profile.id)
        .order_by(ListingMatch.created_at.desc(), ListingMatch.id.desc())
        .limit(10)
        .all()
    )
    return {
        "total": sum(counts.values()),
        "lead_created": counts.get("lead_created", 0),
        "pending": counts.get("pending", 0),
        "recent": recent,
    }


def catalogue_stats() -> dict:
    total = Listing.query.filter(Listing.is_active.is_(True)).count()
    analysed = Listing.query.filter(Listing.is_active.is_(True), Listing.analysis_status == "done").count()
    return {"active": total, "analysed": analysed, "pending_analysis": total - analysed}
