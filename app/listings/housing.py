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


def company_group(listing) -> str:
    """The grouping value: the company key, or the listing alone."""
    return listing.company_key or f"l:{listing.id}"


def _refresh_keys(session, _flush_context, _instances) -> None:
    """Keep the keys in step with the name, city and Y-tunnus, whichever code
    path changed them (crawler, analyzer, import, tests)."""
    from app.listings.models import Listing

    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Listing):
            continue
        # "" = processed, no name (NULL marks rows stored before keys existed)
        name = name_key(obj.taloyhtio, obj.kaupunki) or ""
        if obj.company_name_key != name:
            obj.company_name_key = name
        plain = plain_company_key(obj)
        current = obj.company_key or ""
        if plain is None:
            if obj.company_key is not None:
                obj.company_key = None
        elif plain.startswith("y:") and current.startswith("n:"):
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
    if year is not None:
        ahead = year - now.year
        if ahead <= 1:
            return 25, f"alkaa {year}"
        if ahead == 2:
            return 20, f"alkaa {year}"
        if ahead <= 4:
            return 12, f"alkaa {year}"
        return 5, f"alkaa {year}"
    if has_timing_text:
        return 10, "suunnitteilla, vuosi ei tiedossa"
    return 6, "ajankohta ei tiedossa"


def score_target(listings, wanted_types: set[str] | None = None, *, now: datetime | None = None) -> dict:
    """How promising one housing company is for the organization, 0–100.

    Parts (max points): confidence of the best wanted renovation (40), how
    soon it starts (25), reachable contacts (15), how many independent signals
    point to it (10) and how fresh the newest signal is (10). Returns
    {"total": int, "parts": [{"key", "label", "points", "max", "detail"}]}.
    """
    now = now or datetime.now(timezone.utc)
    listings = [l for l in listings if l is not None]
    renovations = [
        r
        for listing in listings
        for r in listing.renovations
        if not wanted_types or r.remonttityyppi in wanted_types
    ]
    best = max(renovations, key=lambda r: (r.confidence, -(r.timing_year or 9999)), default=None)
    parts = []

    confidence = best.confidence if best else 0
    parts.append(
        {
            "key": "confidence",
            "label": "Varmuus",
            "points": round(confidence * 40 / 100),
            "max": 40,
            "detail": f"{best.remonttityyppi} {confidence} %" if best else "ei osuvaa remonttia",
        }
    )

    years = [r.timing_year for r in renovations if r.timing_year]
    soonest = min(years) if years else None
    points, detail = _timing_points(soonest, any((r.timing or "").strip() for r in renovations), now)
    parts.append({"key": "timing", "label": "Ajoitus", "points": points if renovations else 0, "max": 25, "detail": detail})

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
    signal_points = 0 if signal_count <= 1 else (6 if signal_count == 2 else 10)
    parts.append(
        {
            "key": "signals",
            "label": "Signaalit",
            "points": signal_points,
            "max": 10,
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


def org_company_listings(organization_id: int, groups: set[str], *, include_reserve: bool = False) -> dict:
    """{company group: [listings]} of the organization's non-reserve matches
    (pending released ones and handled ones) for the given groups."""
    from app.listings.models import Listing, ListingMatch
    from app.listings.services import _now
    from app.listings.target_services import fetch_enabled

    if not groups:
        return {}
    keys = {g for g in groups if not g.startswith("l:")}
    singles = {int(g[2:]) for g in groups if g.startswith("l:")}
    query = (
        db.session.query(Listing)
        .join(ListingMatch, ListingMatch.listing_id == Listing.id)
        .filter(ListingMatch.organization_id == organization_id, Listing.is_active.is_(True))
    )
    criteria = []
    if keys:
        criteria.append(Listing.company_key.in_(keys))
    if singles:
        criteria.append(Listing.id.in_(singles))
    query = query.filter(db.or_(*criteria))
    if fetch_enabled() and not include_reserve:
        query = query.filter(
            db.or_(
                ListingMatch.status != "pending",
                db.and_(ListingMatch.released_at.isnot(None), ListingMatch.released_at <= _now()),
            )
        )
    result: dict[str, list] = {}
    for listing in query.distinct().all():
        group = company_group(listing)
        if group in groups and all(l.id != listing.id for l in result.get(group, [])):
            result.setdefault(group, []).append(listing)
    return result


def refresh_scores(organization_id: int | None = None, *, now: datetime | None = None) -> int:
    """Recompute the stored score of every open (pending) match: the sort key
    of Kohteet "Parhaat ensin". Timing and freshness drift with time, so the
    scheduler runs this regularly. Returns how many scores changed."""
    from app.listings.models import ListingMatch
    from app.listings.target_services import organization_wanted_types

    now = now or datetime.now(timezone.utc)
    query = ListingMatch.query.filter(ListingMatch.status == "pending")
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
        listings = org_company_listings(org_id, groups, include_reserve=True)
        for match in matches:
            company = listings.get(company_group(match.listing)) or [match.listing]
            total = score_target(company, wanted, now=now)["total"]
            if match.score != total:
                match.score = total
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
        url = listing.url if (listing.url or "").startswith("http") else None
        add(seen, "listing", f"Ilmoitus löytyi ({source_label(listing.source)})", where, url)
        for renovation in listing.renovations:
            detail = " · ".join(
                p
                for p in (
                    renovation.timing and f"ajankohta {renovation.timing}",
                    f"varmuus {renovation.confidence} %",
                    renovation.evidence and f"“{renovation.evidence}”",
                )
                if p
            )
            add(listing.analyzed_at or seen, "renovation", f"Remonttimaininta: {renovation.remonttityyppi}", detail)
        if listing.has_contact and listing.contact_source:
            add(
                listing.analyzed_at or seen,
                "contact",
                "Isännöitsijän yhteystiedot löytyivät",
                f"lähde: {source_label(listing.contact_source)}",
            )
        if listing.removed_at:
            add(listing.removed_at, "removed", "Ilmoitus poistui lähteestä", where)

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
