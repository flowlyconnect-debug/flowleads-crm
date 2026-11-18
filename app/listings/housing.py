"""One housing company (taloyhtiö), however many listings and sources mention it.

Every catalogue listing gets a `company_key`. Listings with the same key are the
same housing company: on Kohteet they are one target, on the housing-company
card their signals form one timeline, and transferring the target moves all of
them into the same lead.

Keys:
    * ``n:<normalised name>|<city>`` when the listing names the housing company
      ("As. Oy Kallio", "Asunto Oy Kallio" and "ASUNTO-OSAKEYHTIÖ KALLIO" in
      Espoo are one company);
    * ``y:<Y-tunnus>`` when it gives only the business ID;
    * NULL when it gives neither (the listing stands alone).

The name comes first on purpose: import files have been seen to carry the
property manager's Y-tunnus in the housing-company column, which would merge
dozens of companies into one. A Y-tunnus only joins a listing without a name to
the named listing with the same Y-tunnus (`reconcile_company_key`).
`company_name_key` is the name key alone ("" = no name).

The module also scores a target 0–100 (`score_target`) and builds the card's
signal timeline (`company_timeline`).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db

_YTUNNUS_RE = re.compile(r"(\d{7})-?(\d)")
# legal-form prefixes that do not distinguish two housing companies
_PREFIXES = (
    ("asunto osakeyhtio ", "as oy "),
    ("asunto oy ", "as oy "),
    ("as oy ", "as oy "),
    ("bostadsaktiebolaget ", "as oy "),
    ("bostads ab ", "as oy "),
    ("kiinteisto osakeyhtio ", "koy "),
    ("kiinteisto oy ", "koy "),
    ("kiint oy ", "koy "),
    ("koy ", "koy "),
)


def fold(value) -> str:
    """Lowercase, accent-free, punctuation as space, single-spaced."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return " ".join(text.split())


def normalize_company_name(name) -> str:
    text = fold(name)
    if not text:
        return ""
    padded = text + " "
    for prefix, canonical in _PREFIXES:
        if padded.startswith(prefix):
            return (canonical + padded[len(prefix):]).strip()
    return text


def normalize_ytunnus(value) -> str | None:
    match = _YTUNNUS_RE.search(str(value or ""))
    return f"{match.group(1)}-{match.group(2)}" if match else None


def name_key(taloyhtio, kaupunki) -> str | None:
    name = normalize_company_name(taloyhtio)
    if not name:
        return None
    return f"n:{name}|{fold(kaupunki)}"[:300]


def plain_company_key(listing) -> str | None:
    named = name_key(listing.taloyhtio, listing.kaupunki)
    if named:
        return named
    ytunnus = normalize_ytunnus(listing.ytunnus)
    return f"y:{ytunnus}" if ytunnus else None


class OutlookRenovation:
    """A row of a housing company's combined outlook (HousingRenovation, app/listings/forecast.py)
    in the shape of a ListingRenovation, so scoring, the PTS strip and the target row read
    either one."""

    def __init__(self, row):
        self.remonttityyppi = row.remonttityyppi
        self.confidence = row.confidence
        self.timing_year = row.year
        self.timing = str(row.year) if row.year else None
        self.stage = row.stage
        self.is_estimate = bool(row.estimate)
        self.sources = list(row.sources or [])
        evidence = row.evidence or []
        self.evidence = evidence[0]["text"] if evidence and evidence[0].get("text") else None
        self.evidence_items = evidence


def company_outlook(listings) -> list | None:
    """The combined outlook of the housing company the listings belong to, or
    None when they are not resolved to one company or its outlook has not
    been computed yet (then the listings' own renovations count)."""
    listings = [l for l in listings if l is not None]
    ids = {getattr(l, "housing_id", None) for l in listings}
    if len(ids) != 1 or None in ids:
        return None
    housing = getattr(listings[0], "housing", None)
    if housing is None or housing.outlook_at is None:
        return None
    return [OutlookRenovation(row) for row in housing.outlook]


def company_renovations(listings) -> list:
    """What is coming at the company: its outlook when there is one, else every
    renovation its listings mention."""
    outlook = company_outlook(listings)
    if outlook is not None:
        return outlook
    return [r for l in listings if l is not None for r in l.renovations]


def company_group(listing) -> str:
    """The grouping value: the company key, or the listing alone."""
    return listing.company_key or f"l:{listing.id}"


def _refresh_keys(session, _flush_context, _instances) -> None:
    """Keep the keys in step with the name, city and Y-tunnus, whichever code
    path changed them (crawler, analyzer, import, tests)."""
    from app.listings.models import Listing, ListingRenovation
    from app.listings.stages import classify_stage

    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, ListingRenovation):
            # decided / planned / need, from the renovation's own words (app/listings/stages.py)
            if obj.stage is None:
                obj.stage = classify_stage(obj.evidence, obj.timing, obj.timing_year)
            continue
        if not isinstance(obj, Listing):
            continue
        # "" = processed, no name (NULL marks rows stored before keys existed)
        name = name_key(obj.taloyhtio, obj.kaupunki) or ""
        if obj.company_name_key != name:
            if obj.company_name_key is not None and obj.housing_id is not None:
                # renamed or moved: resolve the taloyhtiö again (housing_registry.resolve_listings)
                obj.housing_id = None
            obj.company_name_key = name
        # resolved to a taloyhtiö across sources (housing_registry): that groups it
        plain = f"h:{obj.housing_id}" if obj.housing_id else plain_company_key(obj)
        current = obj.company_key or ""
        if plain is None:
            if obj.company_key is not None:
                obj.company_key = None
        elif plain.startswith("y:") and current.startswith("n:") and not obj.housing_id:
            pass  # nameless listing joined to the named company of its Y-tunnus
        elif current != plain:
            obj.company_key = plain


def install_listeners() -> None:
    """Called once from create_app (the scoped session class is global)."""
    from sqlalchemy.orm import Session

    if not event.contains(Session, "before_flush", _refresh_keys):
        event.listen(Session, "before_flush", _refresh_keys)


def reconcile_company_key(listing) -> bool:
    """Join listings that give only the housing company's Y-tunnus to the
    listing that names the company with the same Y-tunnus. Returns True when
    anything changed (the caller commits)."""
    from app.listings.models import Listing

    ytunnus = normalize_ytunnus(listing.ytunnus)
    if not ytunnus:
        return False
    same_id = Listing.query.filter(Listing.ytunnus.isnot(None), Listing.id != listing.id).filter(
        Listing.ytunnus.like(f"%{ytunnus[:7]}%")
    )
    if listing.company_name_key:
        other_names = {
            other.company_name_key
            for other in same_id.filter(Listing.company_name_key != "").all()
            if normalize_ytunnus(other.ytunnus) == ytunnus
        }
        if other_names - {listing.company_name_key}:
            return False  # the Y-tunnus is shared by several names: not reliable
        changed = False
        for other in same_id.filter(Listing.company_name_key == "").all():
            if normalize_ytunnus(other.ytunnus) == ytunnus and other.company_key != listing.company_key:
                other.company_key = listing.company_key
                changed = True
        return changed
    named = [
        other
        for other in same_id.filter(Listing.company_name_key != "").order_by(Listing.id.asc()).all()
        if normalize_ytunnus(other.ytunnus) == ytunnus
    ]
    # only when the Y-tunnus points to exactly one named company
    keys = {other.company_key for other in named}
    if len(keys) == 1:
        key = keys.pop()
        if listing.company_key != key:
            listing.company_key = key
            return True
    return False


def backfill_company_keys(*, batch_size: int = 1000) -> int:
    """Give a key to listings stored before keys existed. Idempotent."""
    from app.listings.models import Listing

    done = 0
    rows = (
        Listing.query.filter(Listing.company_name_key.is_(None))
        .limit(batch_size)
        .all()
    )
    for listing in rows:
        listing.company_name_key = name_key(listing.taloyhtio, listing.kaupunki) or ""
        listing.company_key = plain_company_key(listing)
        done += 1
    db.session.flush()
    for listing in rows:
        reconcile_company_key(listing)
    if rows:
        db.session.commit()
    return done


# --- score -------------------------------------------------------------------

SCORE_PARTS = (
    ("confidence", "Varmuus", 40),
    ("timing", "Ajoitus", 25),
    ("contact", "Yhteystiedot", 15),
    ("signals", "Signaalit", 10),
    ("fresh", "Tuoreus", 10),
)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _timing_points(year: int | None, has_timing_text: bool, now: datetime) -> tuple[int, str]:
    """Out of 20: this or next year 20, in two years 16, in 3-4 years 10."""
    if year is not None:
        ahead = year - now.year
        if ahead <= 1:
            return 20, f"alkaa {year}"
        if ahead == 2:
            return 16, f"alkaa {year}"
        if ahead <= 4:
            return 10, f"alkaa {year}"
        return 4, f"alkaa {year}"
    if has_timing_text:
        return 8, "suunnitteilla, vuosi ei tiedossa"
    return 5, "ajankohta ei tiedossa"


def score_target(listings, wanted_types: set[str] | None = None, *, now: datetime | None = None) -> dict:
    """How promising one housing company is for the organization, 0–100.

    Parts (max points): how far the furthest wanted renovation is (stage:
    decided 20, planned 10, only a need 0; app/listings/stages.py), confidence
    of the best wanted renovation (30), how soon it starts (20), reachable
    contacts (15), how fresh the newest signal is (10) and how many
    independent signals point to it (5). Returns
    {"total": int, "parts": [{"key", "label", "points", "max", "detail"}]}.
    """
    from app.listings.stages import STAGE_LABELS, STAGE_POINTS, best_stage

    now = now or datetime.now(timezone.utc)
    listings = [l for l in listings if l is not None]
    renovations = [r for r in company_renovations(listings) if not wanted_types or r.remonttityyppi in wanted_types]
    best = max(renovations, key=lambda r: (r.confidence, -(r.timing_year or 9999)), default=None)
    parts = []

    stage = best_stage(renovations)
    parts.append(
        {
            "key": "stage",
            "label": "Vaihe",
            "points": STAGE_POINTS.get(stage, 0),
            "max": 20,
            "detail": STAGE_LABELS[stage].lower() if stage else "ei osuvaa remonttia",
            "stage": stage,
        }
    )

    confidence = best.confidence if best else 0
    parts.append(
        {
            "key": "confidence",
            "label": "Varmuus",
            "points": round(confidence * 30 / 100),
            "max": 30,
            "detail": f"{best.remonttityyppi} {confidence} %" if best else "ei osuvaa remonttia",
        }
    )

    years = [r.timing_year for r in renovations if r.timing_year]
    soonest = min(years) if years else None
    points, detail = _timing_points(soonest, any((r.timing or "").strip() for r in renovations), now)
    parts.append({"key": "timing", "label": "Ajoitus", "points": points if renovations else 0, "max": 20, "detail": detail})

    phone = any((l.isannoitsija_puh or "").strip() or (getattr(l, "hallitus_pj_puh", None) or "").strip() for l in listings)
    email = any((l.isannoitsija_email or "").strip() or (getattr(l, "hallitus_pj_email", None) or "").strip() for l in listings)
    named = any(
        (l.isannoitsija or "").strip() or (getattr(l, "hallitus_pj", None) or "").strip() for l in listings
    )
    if phone and email:
        contact, contact_detail = 15, "puhelin ja sähköposti"
    elif phone or email:
        contact, contact_detail = 10, "puhelin" if phone else "sähköposti"
    elif named:
        contact, contact_detail = 4, "vain nimi"
    else:
        contact, contact_detail = 0, "ei yhteystietoja"
    parts.append({"key": "contact", "label": "Yhteystiedot", "points": contact, "max": 15, "detail": contact_detail})

    signal_count = len(listings) + max(0, len({r.remonttityyppi for r in renovations}) - 1)
    signal_points = 0 if signal_count <= 1 else (3 if signal_count == 2 else 5)
    parts.append(
        {
            "key": "signals",
            "label": "Signaalit",
            "points": signal_points,
            "max": 5,
            "detail": f"{signal_count} {'signaali' if signal_count == 1 else 'signaalia'}",
        }
    )

    seen = [
        _aware(l.published_at or l.first_seen_at) for l in listings if (l.published_at or l.first_seen_at)
    ]
    newest = max(seen) if seen else None
    if newest is None:
        fresh, fresh_detail = 2, "päiväys ei tiedossa"
    else:
        age = (now - newest).days
        fresh = 10 if age <= 30 else (6 if age <= 90 else 2)
        fresh_detail = "alle 30 pv" if age <= 30 else ("alle 3 kk" if age <= 90 else "yli 3 kk")
    parts.append({"key": "fresh", "label": "Tuoreus", "points": fresh, "max": 10, "detail": fresh_detail})

    return {"total": max(0, min(100, sum(p["points"] for p in parts))), "parts": parts}


# Classes of a target, decided when it is found (before the customer sees it).
# A, B and C are renovations the housing company has decided or planned:
# A = hot (strong, soon, reachable), B = good, C = keep an eye on. T = only a
# need (no decision, no plan: an age estimate, oil heating, "edessä joskus"):
# Kohteet keeps these on a tab of their own (Tarve), never mixed with the rest.
TIERS = ("A", "B", "C")
NEED_TIER = "T"
TIER_LABELS = {"A": "Kuuma", "B": "Hyvä", "C": "Seurattava", "T": "Tarve"}
TIER_A_SCORE = 70
TIER_B_SCORE = 50


def is_estimate_company(listings, wanted_types=None) -> bool:
    """Only register estimates behind what the organization wants here: never class A."""
    renovations = [r for r in company_renovations(listings) if not wanted_types or r.remonttityyppi in wanted_types]
    if renovations:
        return all(getattr(r, "is_estimate", False) for r in renovations)
    return all(getattr(l, "is_estimate", False) for l in listings)


def classify(score: dict, *, estimate: bool = False) -> str:
    """The class of a scored housing company (score = score_target(...)).
    Only a need (stage "need") -> T. A needs a high score, a renovation within
    two years (timing >= 16 of 20) and a phone or an email (contact >= 10 of
    15); an estimate from the public registers is never A."""
    parts = {p["key"]: p for p in score["parts"]}
    stage = parts.get("stage", {}).get("stage")
    if stage in (None, "need"):
        return NEED_TIER
    points = {key: part["points"] for key, part in parts.items()}
    total = score["total"]
    if not estimate and total >= TIER_A_SCORE and points.get("timing", 0) >= 16 and points.get("contact", 0) >= 10:
        return "A"
    if total >= TIER_B_SCORE:
        return "B"
    return "C"


def tier_allowed(tier: str | None, min_tier: str) -> bool:
    """True when `tier` is `min_tier` or better (A is best)."""
    if tier not in TIERS:
        return False
    return TIERS.index(tier) <= TIERS.index(min_tier if min_tier in TIERS else "B")


def score_label(total: int) -> str:
    if total >= 75:
        return "Erinomainen"
    if total >= 55:
        return "Hyvä"
    if total >= 35:
        return "Kohtalainen"
    return "Heikko"


def score_level(total: int) -> str:
    """CSS modifier for the score badge."""
    if total >= 75:
        return "high"
    if total >= 55:
        return "good"
    if total >= 35:
        return "mid"
    return "low"


def org_company_listings(organization_id: int, groups: set[str]) -> dict:
    """{company group: [listings]} of the organization's matches for the
    given groups."""
    from app.listings.models import HousingCompany, Listing, ListingMatch

    if not groups:
        return {}
    keys = {g for g in groups if not g.startswith("l:")}
    singles = {int(g[2:]) for g in groups if g.startswith("l:")}
    query = (
        db.session.query(Listing)
        .join(ListingMatch, ListingMatch.listing_id == Listing.id)
        .filter(ListingMatch.organization_id == organization_id, Listing.is_active.is_(True))
        # scoring reads every listing's renovations and its company's outlook: few queries
        .options(selectinload(Listing.renovations), selectinload(Listing.housing).selectinload(HousingCompany.outlook))
    )
    criteria = []
    if keys:
        criteria.append(Listing.company_key.in_(keys))
    if singles:
        criteria.append(Listing.id.in_(singles))
    query = query.filter(db.or_(*criteria))
    result: dict[str, list] = {}
    for listing in query.distinct().all():
        group = company_group(listing)
        if group in groups and all(l.id != listing.id for l in result.get(group, [])):
            result.setdefault(group, []).append(listing)
    return result


def refresh_scores(organization_id: int | None = None, *, now: datetime | None = None) -> int:
    """Recompute the stored score and class of every open (pending) match:
    the sort key of Kohteet "Sopivimmat ensin" and what release_targets lets
    through. Timing and freshness drift with time, so the scheduler runs this
    hourly. Returns how many changed."""
    from app.listings.models import HousingCompany, Listing, ListingMatch
    from app.listings.target_services import organization_wanted_types

    now = now or datetime.now(timezone.utc)
    query = ListingMatch.query.filter(ListingMatch.status == "pending").options(
        joinedload(ListingMatch.listing).selectinload(Listing.renovations),
        joinedload(ListingMatch.listing).selectinload(Listing.housing).selectinload(HousingCompany.outlook),
    )
    if organization_id is not None:
        query = query.filter(ListingMatch.organization_id == organization_id)
    by_org: dict[int, list] = {}
    for match in query.all():
        if match.listing is not None:
            by_org.setdefault(match.organization_id, []).append(match)
    changed = 0
    for org_id, matches in by_org.items():
        wanted = organization_wanted_types(org_id)
        groups = {company_group(m.listing) for m in matches}
        listings = org_company_listings(org_id, groups)
        for match in matches:
            company = listings.get(company_group(match.listing)) or [match.listing]
            score = score_target(company, wanted, now=now)
            tier = classify(score, estimate=is_estimate_company(company, wanted))
            if match.score != score["total"] or match.tier != tier:
                match.score = score["total"]
                match.tier = tier
                changed += 1
    if changed:
        db.session.commit()
    return changed


# --- timeline -------------------------------------------------------------------

SOURCE_LABELS = {"oikotie": "Oikotie", "import": "Tuonti"}


def source_label(source: str | None) -> str:
    return SOURCE_LABELS.get(source or "", (source or "Lähde").capitalize())


def company_timeline(organization_id: int, listings, matches, leads, *, limit: int = 60) -> list[dict]:
    """Everything that has happened around one housing company, newest first:
    listings found and removed, renovation mentions, contacts found, the
    target becoming visible, moved to a lead or hidden, and the sales work
    logged on its leads. Each item: {"at", "kind", "title", "detail", "url"}."""
    from app.leads.models import Activity

    events: list[dict] = []

    def add(at, kind, title, detail="", url=None):
        if at is not None:
            events.append({"at": _aware(at), "kind": kind, "title": title, "detail": detail, "url": url})

    for listing in listings:
        seen = listing.published_at or listing.first_seen_at
        where = ", ".join(p for p in (listing.osoite, listing.kaupunki) if p)
        estimate = bool(getattr(listing, "is_estimate", False))
        # where the listing came from is not shown to customers
        add(seen, "listing", "Rakennus julkisista rekistereistä" if estimate else "Myynti-ilmoitus löytyi", where)
        for renovation in listing.renovations:
            evidence = renovation.evidence and (renovation.evidence if estimate else f"“{renovation.evidence}”")
            detail = " · ".join(
                p
                for p in (
                    renovation.timing and f"ajankohta {renovation.timing}",
                    f"varmuus {renovation.confidence} %",
                    evidence,
                )
                if p
            )
            title = f"Ennuste: {renovation.remonttityyppi}" if estimate else f"Remonttimaininta: {renovation.remonttityyppi}"
            add(listing.analyzed_at or seen, "renovation", title, detail)
        if listing.has_contact and listing.contact_source:
            add(listing.analyzed_at or seen, "contact", "Isännöitsijän yhteystiedot löytyivät")
        if listing.removed_at:
            add(listing.removed_at, "removed", "Ennuste poistui" if estimate else "Ilmoitus poistui myynnistä", where)

    for match in matches:
        if match.released_at and match.status == "pending":
            add(match.released_at, "target", "Kohde tuli saataville Kohteet-sivulle")
        if match.lead_created_at and match.status == "lead_created":
            add(match.lead_created_at, "lead", "Siirretty liidiksi")
        if getattr(match, "dismissed_at", None) and match.status == "dismissed":
            add(match.dismissed_at, "hidden", "Piilotettu Kohteet-sivulta")

    lead_ids = [lead.id for lead in leads]
    if lead_ids:
        labels = {
            "call": "Soitto",
            "email_sent": "Sähköposti lähetetty",
            "email_reply_received": "Vastaus saatu",
            "note": "Muistiinpano",
            "stage_changed": "Myyntivaihe muuttui",
            "task_completed": "Tehtävä tehty",
            "created": "Liidi luotu",
        }
        activities = (
            Activity.query.filter(
                Activity.organization_id == organization_id, Activity.lead_id.in_(lead_ids)
            )
            .order_by(Activity.created_at.desc())
            .limit(limit)
            .all()
        )
        for activity in activities:
            if activity.type not in labels:
                continue
            add(activity.created_at, "sales", labels[activity.type], (activity.content or "")[:200])

    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


# Typical renovation intervals for Finnish apartment buildings (years from
# completion), used only to hint at a need the sources do not mention.
_AGE_HINTS = (
    ("Putkiremontti", 50, "Putkiremontti on tyypillisesti ajankohtainen noin 50 vuotta valmistumisesta."),
    ("Katto- ja vesikattoremontti", 35, "Vesikatto uusitaan tyypillisesti 30–40 vuoden välein."),
    ("Julkisivuremontti", 40, "Julkisivu ja ikkunat korjataan tyypillisesti noin 40 vuoden iässä."),
    ("Sähkö- ja tietoliikennejärjestelmät", 50, "Sähköverkko on tyypillisesti uusimisiässä noin 50 vuoden kohdalla."),
)


def age_based_hints(build_year: int | None, known_types: set[str], wanted_types: set[str], *, now: datetime | None = None) -> list[dict]:
    """Renovations the building's age suggests but no source mentions.
    Estimates, never facts: shown separately and not part of the score."""
    if not build_year:
        return []
    now = now or datetime.now(timezone.utc)
    hints = []
    for rtype, interval, reason in _AGE_HINTS:
        if rtype in known_types or (wanted_types and rtype not in wanted_types):
            continue
        due = build_year + interval
        if due <= now.year + 5:
            hints.append(
                {
                    "type": rtype,
                    "due": max(due, now.year),
                    "overdue": due < now.year,
                    "reason": reason,
                }
            )
    return hints
