"""Kohteet: the catalogue listings an organization's search profiles match and
that are not leads yet.

A target is a `ListingMatch` in status "pending". Nothing becomes a lead on its
own: a user moves targets to leads here (`transfer_targets`) or hides the ones
they do not want (`dismiss_targets`).
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import aliased, contains_eager, joinedload

from app.extensions import db
from app.listings.housing import company_group, org_company_listings, score_label, score_level, score_target
from app.listings.models import Listing, ListingMatch, ListingRenovation, SavedTargetSearch
from app.listings.pts import pts_strip
from app.listings.services import _best_renovation, _merge_into_lead, _now, materialize_match
from app.search.constants import FINNISH_REGIONS
from app.search.municipalities import region_municipalities

PER_PAGE = 25
MAX_BULK = 200
# tab on the page -> match status
TABS = {"available": "pending", "hidden": "dismissed"}
_LEAD_STATUSES = ("lead_created", "merged")


def _visible_match_ids(
    organization_id: int,
    status: str,
    *,
    profile_id: int | None = None,
):
    """One match id per housing company: a listing matched by several of the
    organization's profiles is one target, so are several listings of the same
    taloyhtiö (app/listings/housing.py), and a company that already has a lead
    is not a target at all."""
    already_lead = db.session.query(ListingMatch.listing_id).filter(
        ListingMatch.organization_id == organization_id,
        ListingMatch.status.in_(_LEAD_STATUSES),
    )
    query = (
        db.session.query(func.min(ListingMatch.id))
        .join(Listing, Listing.id == ListingMatch.listing_id)
        .filter(
            ListingMatch.organization_id == organization_id,
            ListingMatch.status == status,
            Listing.is_active.is_(True),
            ListingMatch.listing_id.notin_(already_lead),
            _not_in_companies(organization_id, _LEAD_STATUSES, with_lead=True),
        )
        .group_by(_group_column())
    )
    if status == "pending":
        # hidden once = hidden for the organization, even when another of its
        # profiles matches the same listing later
        hidden = db.session.query(ListingMatch.listing_id).filter(
            ListingMatch.organization_id == organization_id,
            ListingMatch.status == "dismissed",
        )
        query = query.filter(
            ListingMatch.listing_id.notin_(hidden), _not_in_companies(organization_id, ("dismissed",))
        )
    if profile_id:
        query = query.filter(ListingMatch.profile_id == profile_id)
    return query


def _group_column():
    """Housing company of a listing, or the listing itself when it names none."""
    return func.coalesce(Listing.company_key, cast(Listing.id, String))


def _not_in_companies(organization_id: int, statuses, *, with_lead: bool = False):
    """Listings outside the housing companies of the organization's matches
    in `statuses` (with an existing lead, when `with_lead`)."""
    other = aliased(Listing)
    keys = (
        db.session.query(other.company_key)
        .join(ListingMatch, ListingMatch.listing_id == other.id)
        .filter(
            ListingMatch.organization_id == organization_id,
            ListingMatch.status.in_(tuple(statuses)),
            other.company_key.isnot(None),
            *([ListingMatch.lead_id.isnot(None)] if with_lead else []),
        )
    )
    return or_(Listing.company_key.is_(None), Listing.company_key.notin_(keys))


def _company_wide(listing_ids):
    """The listings in `listing_ids` and every listing of their housing
    companies: a filter on renovations or contacts is true for the company
    when any of its listings satisfies it."""
    other = aliased(Listing)
    keys = db.session.query(other.company_key).filter(other.id.in_(listing_ids), other.company_key.isnot(None))
    return or_(Listing.id.in_(listing_ids), Listing.company_key.in_(keys))


def count_targets(organization_id: int, status: str = "pending") -> int:
    return _visible_match_ids(organization_id, status).count()


def locked_target_summary(organization_id: int, *, now: datetime | None = None, top: int = 4) -> dict:
    """What a customer without target access may see (targets/locked.html):
    counts only, never a housing company, address or manager. The total, how
    many arrived this week, and the largest renovation types and regions."""
    from datetime import timedelta

    now = now or _now()
    visible = _visible_match_ids(organization_id, "pending")
    total = visible.count()
    if not total:
        return {"total": 0, "new_week": 0, "by_type": [], "by_region": []}
    rows = (
        db.session.query(ListingMatch.id, ListingMatch.created_at, Listing.id, Listing.maakunta)
        .join(Listing, Listing.id == ListingMatch.listing_id)
        .filter(ListingMatch.id.in_(visible))
        .all()
    )
    week_ago = now - timedelta(days=7)
    new_week = 0
    regions: dict[str, int] = {}
    listing_ids = []
    for _, created_at, listing_id, maakunta in rows:
        listing_ids.append(listing_id)
        if created_at is not None:
            aware = created_at if created_at.tzinfo else created_at.replace(tzinfo=now.tzinfo)
            if aware > week_ago:
                new_week += 1
        if maakunta:
            regions[maakunta] = regions.get(maakunta, 0) + 1
    wanted = organization_wanted_types(organization_id)
    types: dict[str, set[int]] = {}
    for listing_id, kind in db.session.query(ListingRenovation.listing_id, ListingRenovation.remonttityyppi).filter(
        ListingRenovation.listing_id.in_(listing_ids)
    ):
        if not wanted or kind in wanted:
            types.setdefault(kind, set()).add(listing_id)

    def _top(counts: dict[str, int]) -> list[dict]:
        ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:top]
        return [{"name": name, "count": count, "share": round(100 * count / total)} for name, count in ordered]

    return {
        "total": total,
        "new_week": new_week,
        "by_type": _top({kind: len(ids) for kind, ids in types.items()}),
        "by_region": _top(regions),
    }


def list_targets(
    organization_id: int,
    *,
    status: str = "pending",
    profile_id: int | None = None,
    search: str | None = None,
    filters: dict | None = None,
    page: int = 1,
    per_page: int = PER_PAGE,
):
    """Paginated targets, newest listings first. Items are ListingMatch rows
    with `listing`, its renovations and `profile` loaded."""
    query = (
        ListingMatch.query.join(Listing, Listing.id == ListingMatch.listing_id)
        .options(
            contains_eager(ListingMatch.listing).selectinload(Listing.renovations),
            joinedload(ListingMatch.profile),
        )
        .filter(ListingMatch.id.in_(_visible_match_ids(organization_id, status, profile_id=profile_id)))
    )
    term = " ".join((search or "").split())
    if term:
        like = f"%{term}%"
        query = query.filter(
            or_(
                Listing.taloyhtio.ilike(like),
                Listing.osoite.ilike(like),
                Listing.kaupunki.ilike(like),
                Listing.isannoitsija.ilike(like),
            )
        )
    query = _apply_target_filters(query, filters or {})
    newest = func.coalesce(Listing.published_at, Listing.first_seen_at)
    sort = (filters or {}).get("sort")
    if sort not in {key for key, _ in SORT_OPTIONS}:
        sort = ""
    if sort == "":
        return _list_by_fit(organization_id, query, page=page, per_page=per_page)
    if sort == "oldest":
        query = query.order_by(newest.asc(), Listing.id.asc())
    elif sort == "city":
        query = query.order_by(func.lower(func.coalesce(Listing.kaupunki, "")).asc(), newest.desc())
    elif sort == "confidence":
        query = query.order_by(ListingMatch.confidence.desc(), newest.desc())
    elif sort == "year":
        first_year = (
            db.session.query(func.min(ListingRenovation.timing_year))
            .filter(ListingRenovation.listing_id == Listing.id)
            .correlate(Listing)
            .scalar_subquery()
        )
        query = query.order_by(first_year.is_(None), first_year.asc(), newest.desc(), Listing.id.desc())
    else:  # "newest"
        query = query.order_by(newest.desc(), Listing.id.desc())
    return query.paginate(page=max(1, page), per_page=per_page, error_out=False)


# The Sopivuus is 0.9 x the target score plus the learned bonus, and the bonus
# only depends on the renovation type, region and build decade, so the best
# Sopivuus a target can reach is known from its stored score (refreshed hourly)
# and those three values. Targets whose best is below the page's last row are
# never scored. The slack covers a stored score that has drifted since the
# refresh (freshness is worth 10 points) and a housing company whose listings
# differ in region or decade.
_FIT_SLACK = 12
_FIT_BATCH = 400


def _list_by_fit(organization_id: int, query, *, page: int, per_page: int):
    """"Sopivimmat ensin": targets by Sopivuus (app/listings/fit_services.py),
    best first, newest first among equals. Only the targets that can still
    reach the requested page are scored, in order of their best possible
    Sopivuus; scoring stops once the next one cannot beat the page's last row."""
    from app.listings.fit_services import ListPage, learn_preferences, score_matches

    base = query.enable_eagerloads(False).order_by(None)
    rows = base.with_entities(ListingMatch.id, ListingMatch.score, Listing.id, Listing.maakunta, Listing.build_year).all()
    wanted = organization_wanted_types(organization_id)
    prefs = learn_preferences(organization_id, wanted)
    types: dict[int, set[str]] = {}
    if prefs.decisions:
        for listing_id, rtype in db.session.query(ListingRenovation.listing_id, ListingRenovation.remonttityyppi).filter(
            ListingRenovation.listing_id.in_(base.with_entities(Listing.id))
        ):
            if not wanted or rtype in wanted:
                types.setdefault(listing_id, set()).add(rtype)

    def best_possible(row) -> float:
        if row[1] is None:
            return float("inf")  # unknown score: has to be computed to be placed
        bonus = prefs.bonus_upper_bound(types.get(row[2]), row[3], row[4]) if prefs.decisions else 0.0
        return 0.9 * row[1] + bonus + _FIT_SLACK + 1  # +1: the Sopivuus is rounded

    candidates = sorted(((best_possible(row), row[0]) for row in rows), key=lambda pair: (-pair[0], pair[1]))
    needed = max(1, page) * per_page
    scored: list[tuple[tuple, ListingMatch]] = []

    def key(match, fit):
        seen = match.listing.published_at or match.listing.first_seen_at
        return (-fit, -(seen.timestamp() if seen else 0), -match.id)

    position = 0
    while position < len(candidates):
        if len(scored) >= needed:
            scored.sort(key=lambda pair: pair[0])
            if candidates[position][0] < -scored[needed - 1][0][0]:
                break
        ids = [match_id for _, match_id in candidates[position : position + _FIT_BATCH]]
        position += len(ids)
        batch = query.filter(ListingMatch.id.in_(ids)).all()
        fits = score_matches(batch, prefs, wanted_types=wanted)
        scored.extend((key(match, fits[match.id][0]), match) for match in batch)
    scored.sort(key=lambda pair: pair[0])
    return ListPage([match for _, match in scored], page, per_page, total=len(candidates))


# Query-string keys of the Kohteet filter bar (kept across actions and pages).
FILTER_KEYS = ("renovation", "region", "city", "year", "building", "contact", "sort")
SORT_OPTIONS = (
    ("", "Sopivimmat ensin"),
    ("newest", "Uusimmat ensin"),
    ("oldest", "Vanhimmat ensin"),
    ("confidence", "Varmimmat ensin"),
    ("city", "Kaupungin mukaan"),
    ("year", "Remontti alkaa ensin"),
)


def _apply_target_filters(query, filters: dict):
    """Narrow the target list. Every filter is optional; unknown values simply
    match nothing, they never raise."""
    renovation = (filters.get("renovation") or "").strip()
    if renovation:
        query = query.filter(
            _company_wide(
                db.session.query(ListingRenovation.listing_id).filter(
                    ListingRenovation.remonttityyppi == renovation
                )
            )
        )
    city = (filters.get("city") or "").strip()
    if city:
        query = query.filter(func.lower(func.trim(Listing.kaupunki)) == city.lower())
    region = (filters.get("region") or "").strip()
    if region:
        query = query.filter(Listing.maakunta == region)
    year = (filters.get("year") or "").strip()
    if year.isdigit():
        # renovation known to start by the end of that year
        query = query.filter(
            _company_wide(
                db.session.query(ListingRenovation.listing_id).filter(
                    ListingRenovation.timing_year.isnot(None),
                    ListingRenovation.timing_year <= int(year),
                    *(
                        [ListingRenovation.remonttityyppi == renovation]
                        if renovation
                        else []
                    ),
                )
            )
        )
    building = (filters.get("building") or "").strip()
    if building:
        query = query.filter(Listing.building_type == building)
    if filters.get("contact") == "1":
        query = query.filter(_company_wide(db.session.query(Listing.id).filter(_has_contact_clause())))
    return query


def _has_contact_clause():
    def filled(column):
        return func.length(func.trim(func.coalesce(column, ""))) > 0

    return or_(
        filled(Listing.isannoitsija_email),
        filled(Listing.isannoitsija_puh),
        *[filled(getattr(Listing, name)) for name in _EXTRA_CONTACT_COLUMNS if hasattr(Listing, name)],
    )


# other reachable people of the housing company (board chair, maintenance)
_EXTRA_CONTACT_COLUMNS = ("hallitus_pj_puh", "hallitus_pj_email", "huoltoyhtio_puh", "huoltoyhtio_email")


def organization_wanted_types(organization_id: int) -> set[str]:
    """Every renovation type the organization's profiles ask for. A listing
    often lists several renovations; the customer only sees and filters by the
    ones it actually does."""
    from app.search.models import SearchProfile

    types: set[str] = set()
    for profile in SearchProfile.query.filter_by(organization_id=organization_id).all():
        types.update(profile.wanted_types)
    return types


def target_filter_options(organization_id: int, status: str = "pending", filters: dict | None = None) -> dict:
    """The values that actually occur among the organization's targets, so the
    filter bar offers only choices that lead somewhere. Cities follow the
    chosen region."""
    filters = filters or {}
    visible = _visible_match_ids(organization_id, status)
    listing_ids = db.session.query(ListingMatch.listing_id).filter(ListingMatch.id.in_(visible))
    wanted = organization_wanted_types(organization_id)

    def distinct(column, *criteria):
        rows = (
            db.session.query(column)
            .filter(Listing.id.in_(listing_ids), column.isnot(None), column != "", *criteria)
            .distinct()
            .all()
        )
        return sorted({str(value).strip() for (value,) in rows if str(value).strip()}, key=str.lower)

    renovation_query = db.session.query(ListingRenovation.remonttityyppi, ListingRenovation.timing_year).filter(
        ListingRenovation.listing_id.in_(listing_ids)
    )
    renovations: set[str] = set()
    years: set[int] = set()
    for rtype, timing_year in renovation_query.distinct().all():
        if wanted and rtype not in wanted:
            continue
        renovations.add(rtype)
        if timing_year:
            # a plan that started in the past ("2024-2028") is "by this year"
            years.add(max(int(timing_year), _now().year))
    region = (filters.get("region") or "").strip()
    if region in FINNISH_REGIONS:
        # every municipality of the region, plus the place names (villages,
        # old municipality names) its targets actually carry
        cities = _merge_place_names(
            region_municipalities(region), distinct(Listing.kaupunki, Listing.maakunta == region)
        )
    else:
        cities = distinct(Listing.kaupunki, *([Listing.maakunta == region] if region else []))
    return {
        "renovations": sorted(renovations),
        "cities": cities,
        # every maakunta is always offered, and a region stored outside the list too
        "regions": list(FINNISH_REGIONS)
        + [r for r in distinct(Listing.maakunta) if r not in FINNISH_REGIONS],
        "buildings": distinct(Listing.building_type),
        "years": [str(y) for y in sorted(years)],
    }


def _merge_place_names(*groups) -> list[str]:
    """One sorted list of place names, duplicates differing only in case or
    surrounding space dropped (the first spelling wins)."""
    seen: dict[str, str] = {}
    for group in groups:
        for name in group:
            cleaned = " ".join(str(name).split())
            if cleaned and cleaned.lower() not in seen:
                seen[cleaned.lower()] = cleaned
    return sorted(seen.values(), key=lambda n: (n.lower(), n))


def target_row(
    match: ListingMatch,
    wanted_types: set[str] | None = None,
    fit: tuple | None = None,
    company_listings: list[Listing] | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """What the page shows for one target: the housing company behind the
    match, with the renovations and contacts of all its listings, its score
    breakdown (app/listings/housing.py) and the Sopivuus with its reasons
    (`fit` = (score, reasons) from app/listings/fit_services.py)."""
    listing = match.listing
    company = list(company_listings or [])
    if all(other.id != listing.id for other in company):
        company.insert(0, listing)
    renovation = _best_renovation(match.profile, listing) if match.profile else None
    if renovation is None and listing.renovations:
        # the profile's filter changed and the match has not been rebuilt yet
        renovation = max(listing.renovations, key=lambda r: r.confidence)
    # every renovation of the company the organization does: one per type,
    # the most confident mention, the match's own first
    best: dict[str, ListingRenovation] = {}
    for item in (r for other in company for r in other.renovations):
        if wanted_types and item.remonttityyppi not in wanted_types:
            continue
        current = best.get(item.remonttityyppi)
        if current is None or item.confidence > current.confidence:
            best[item.remonttityyppi] = item
    if renovation is not None and renovation.remonttityyppi in best:
        if best[renovation.remonttityyppi].confidence <= renovation.confidence:
            best[renovation.remonttityyppi] = renovation
        renovation = best[renovation.remonttityyppi]
    renovations = sorted(best.values(), key=lambda r: (r is not renovation, -r.confidence)) or (
        [renovation] if renovation else []
    )
    # contacts: the first listing of the company that has them
    contact = next((other for other in company if other.has_contact), None) or next(
        (other for other in company if other.isannoitsija), listing
    )
    from app.listings.contacts import company_contacts

    score = score_target(company, wanted_types, now=now)
    seen = [other.published_at or other.first_seen_at for other in company if other.published_at or other.first_seen_at]
    return {
        "match": match,
        "listing": listing,
        "contact": contact,
        "people": company_contacts(company),
        "company_listings": company,
        "signal_count": len(company),
        # the company's listings are all register buildings: "rakennusta", not "ilmoitusta"
        "all_estimates": all(other.is_estimate for other in company),
        "title": listing.taloyhtio or listing.osoite or listing.external_id,
        "location": ", ".join(
            p for p in ((listing.osoite if listing.taloyhtio else None), listing.kaupunki or listing.maakunta) if p
        ),
        "renovation": renovation,
        "renovations": renovations,
        # every renovation shown is an estimate from the public registers
        "estimate": bool(renovations) and all(r.is_estimate for r in renovations),
        "seen_at": max(seen, key=lambda value: value.replace(tzinfo=None)) if seen else None,
        "base_score": score["total"],
        "score": fit[0] if fit else score["total"],
        "score_parts": score["parts"],
        "score_label": score_label(fit[0] if fit else score["total"]),
        "score_level": score_level(fit[0] if fit else score["total"]),
        "fit": fit[0] if fit else None,
        "fit_reasons": fit[1] if fit else [],
        "pts": pts_strip(renovations),
    }


def target_rows(organization_id: int, matches, wanted_types: set[str] | None = None, *, with_fit: bool = True) -> list[dict]:
    """Rows for a page of targets, with the Sopivuus (learned from the
    organization's earlier decisions). Stores the base score of each housing
    company (ListingMatch.score, used by the scheduler and reports)."""
    from app.listings.fit_services import learn_preferences, score_matches

    now = _now()
    matches = list(matches)
    groups = {company_group(match.listing) for match in matches}
    listings = org_company_listings(organization_id, groups)
    fits = (
        score_matches(matches, learn_preferences(organization_id, wanted_types), company_listings=listings, wanted_types=wanted_types)
        if with_fit
        else {}
    )
    rows = []
    changed = False
    for match in matches:
        row = target_row(match, wanted_types, fits.get(match.id), listings.get(company_group(match.listing)), now=now)
        if match.score != row["base_score"]:
            match.score = row["base_score"]
            changed = True
        rows.append(row)
    if changed:
        db.session.commit()
    return rows


def _company_matches(match: ListingMatch, statuses) -> list[ListingMatch]:
    """The organization's matches for the other listings of the same housing
    company, in `statuses`."""
    key = match.listing.company_key if match.listing is not None else None
    if not key:
        return []
    return (
        ListingMatch.query.join(Listing, Listing.id == ListingMatch.listing_id)
        .filter(
            ListingMatch.organization_id == match.organization_id,
            ListingMatch.listing_id != match.listing_id,
            Listing.company_key == key,
            ListingMatch.status.in_(tuple(statuses)),
        )
        .order_by(ListingMatch.id.asc())
        .all()
    )


def _own_matches(organization_id: int, match_ids, statuses) -> list[ListingMatch]:
    ids = []
    for raw in list(match_ids or [])[:MAX_BULK]:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        return []
    return (
        ListingMatch.query.filter(
            ListingMatch.organization_id == organization_id,
            ListingMatch.id.in_(ids),
            ListingMatch.status.in_(statuses),
        )
        .order_by(ListingMatch.id.asc())
        .all()
    )


def _siblings(match: ListingMatch) -> list[ListingMatch]:
    """The organization's other open matches for the same listing (it matched
    several profiles) and for the other listings of the same housing company."""
    same_listing = ListingMatch.query.filter(
        ListingMatch.organization_id == match.organization_id,
        ListingMatch.listing_id == match.listing_id,
        ListingMatch.id != match.id,
        ListingMatch.status.in_(("pending", "dismissed")),
    ).all()
    return same_listing + _company_matches(match, ("pending", "dismissed"))


def transfer_targets(
    organization_id: int,
    match_ids,
    *,
    now: datetime | None = None,
    assigned_to: int | None = None,
) -> dict:
    """Move the given targets to leads. Another apartment of a housing company
    that already has a lead joins that lead. Returns counts and the lead ids."""
    now = now or _now()
    result = {"created": 0, "merged": 0, "failed": 0, "lead_ids": []}
    ids = [m.id for m in _own_matches(organization_id, match_ids, ("pending", "dismissed"))]
    for match_id in ids:
        match = db.session.get(ListingMatch, match_id)
        if match is None or match.status not in ("pending", "dismissed"):
            continue  # handled as a sibling of an earlier one
        if match.listing is None or not match.listing.is_active or match.profile is None:
            result["failed"] += 1
            continue
        outcome, lead = materialize_match(match, now=now)
        if lead is None:
            result["failed"] += 1
            continue
        result["created" if outcome == "created" else "merged"] += 1
        if outcome == "created" and assigned_to:
            lead.assigned_to = assigned_to  # the owner picked in the transfer bar
        if outcome == "created":
            from app.leads.services import announce_new_lead

            db.session.flush()
            announce_new_lead(lead, organization_id)
        if lead.id not in result["lead_ids"]:
            result["lead_ids"].append(lead.id)
        match = db.session.get(ListingMatch, match_id)
        logged: set[int] = {match.listing_id}
        for sibling in _siblings(match):
            if sibling.listing_id in logged:
                sibling.status = "merged"
                sibling.lead_id = lead.id
                sibling.lead_created_at = now
            else:
                # another listing of the same housing company: one lead, and
                # the lead's history tells where the extra signal came from
                _merge_into_lead(sibling, sibling.listing, lead, now)
                logged.add(sibling.listing_id)
        db.session.commit()
    return result



def sequences_for_transfer(organization_id: int) -> list:
    """Active email sequences with at least one step: the ones a transfer can
    start for the new leads. None when the organization does not have the
    Sähköpostisekvenssit module."""
    from app.core.features import organization_has_feature
    from app.sequences.models import EmailSequence, EmailSequenceStep

    if not organization_has_feature(organization_id, "sequences"):
        return []

    has_steps = (
        db.session.query(EmailSequenceStep.id)
        .filter(EmailSequenceStep.sequence_id == EmailSequence.id)
        .exists()
    )
    return (
        EmailSequence.query.filter(
            EmailSequence.organization_id == organization_id,
            EmailSequence.is_active.is_(True),
            has_steps,
        )
        .order_by(EmailSequence.name.asc())
        .all()
    )


def enroll_transferred_leads(
    organization_id: int, lead_ids, sequence_id: int, *, enrolled_by: int | None = None
) -> dict:
    """Start one sequence for the leads a transfer created or joined. A lead
    without an email address, an unsubscribed one and one already in the
    sequence are skipped and counted. Returns the counts."""
    from app.leads.models import Lead
    from app.sequences.services import SequenceService, SequenceServiceError

    result = {"enrolled": 0, "no_email": 0, "already": 0, "unsubscribed": 0}
    sequence = next((s for s in sequences_for_transfer(organization_id) if s.id == sequence_id), None)
    if sequence is None or not lead_ids:
        return result
    leads = Lead.query.filter(Lead.organization_id == organization_id, Lead.id.in_(list(lead_ids))).all()
    for lead in leads:
        if not (lead.email or "").strip():
            result["no_email"] += 1
            continue
        try:
            SequenceService.enroll_lead(
                lead.id, sequence.id, enrolled_by=enrolled_by, organization_id=organization_id
            )
        except SequenceServiceError as exc:
            if exc.code == "duplicate_enrollment":
                result["already"] += 1
            elif exc.code == "unsubscribed":
                result["unsubscribed"] += 1
            else:
                raise
            continue
        result["enrolled"] += 1
    db.session.commit()
    return result

def _set_status(organization_id: int, match_ids, *, from_status: str, to_status: str) -> int:
    """Hide or restore whole housing companies: every match of the company's
    listings changes with the one the user clicked."""
    changed = 0
    now = _now()
    for match in _own_matches(organization_id, match_ids, (from_status,)):
        if match.status != from_status:
            continue  # already changed as a sibling of an earlier one
        changed += 1
        for item in [match, *_siblings(match)]:
            if item.status == from_status:
                item.status = to_status
                item.dismissed_at = now if to_status == "dismissed" else None
    db.session.commit()
    return changed


def dismiss_targets(organization_id: int, match_ids) -> int:
    """Hide targets the customer does not want. They stay hidden when the
    profile is rematched and can be restored."""
    return _set_status(organization_id, match_ids, from_status="pending", to_status="dismissed")


def restore_targets(organization_id: int, match_ids) -> int:
    return _set_status(organization_id, match_ids, from_status="dismissed", to_status="pending")


# --- daily digest of new targets ---------------------------------------------


def count_new_targets(organization_id: int, since: datetime) -> int:
    """Available targets whose match appeared after `since`."""
    return (
        db.session.query(func.count(ListingMatch.id))
        .filter(
            ListingMatch.id.in_(_visible_match_ids(organization_id, "pending")),
            ListingMatch.created_at > since,
        )
        .scalar()
        or 0
    )


def send_target_digests(*, now: datetime | None = None) -> int:
    """Tell each organization's users how many new targets arrived since the
    last digest: an in-app notification for everyone and one email per user.
    Organizations with nothing new, or with the digest switched off, get
    nothing. Returns the number of organizations notified."""
    from datetime import timedelta

    from flask import current_app

    from app.notifications.services import NotificationService
    from app.billing.services import targets_locked
    from app.tasks.settings import get_organization_settings
    from app.users.models import Organization, User

    now = now or _now()
    notified = 0
    for organization in Organization.query.filter_by(is_active=True).all():
        settings = get_organization_settings(organization.id)
        if not settings.targets_digest_enabled or targets_locked(organization.id):
            continue
        since = settings.targets_digest_sent_at or (now - timedelta(days=1))
        if since.tzinfo is None:
            since = since.replace(tzinfo=now.tzinfo)
        new_count = count_new_targets(organization.id, since)
        if not new_count:
            continue
        total = count_targets(organization.id)
        users = User.query.filter(
            User.organization_id == organization.id,
            User.is_active.is_(True),
            User.role.in_(("admin", "user")),
        ).all()
        if not users:
            continue
        title = "1 uusi kohde" if new_count == 1 else f"{new_count} uutta kohdetta"
        message = (
            f"Hakuprofiiliisi osui {title.lower()}. Kohteet-sivulla odottaa yhteensä "
            f"{total} {'kohde' if total == 1 else 'kohdetta'}."
        )
        for user in users:
            NotificationService.create(
                user_id=user.id,
                organization_id=organization.id,
                type="targets_new",
                title=title,
                message=message,
                link="/targets",
            )
        settings.targets_digest_sent_at = now
        db.session.commit()
        notified += 1
        try:
            _email_target_digest(current_app, organization, users, title, message)
        except Exception:  # an email problem must not stop the other organizations
            current_app.logger.exception("Target digest email failed for org %s", organization.id)
    return notified


def _email_target_digest(app, organization, users, title: str, message: str) -> None:
    from flask_mail import Message

    from app.email.services import send_system_message

    base_url = (app.config.get("APP_BASE_URL") or "").rstrip("/")
    link = f"{base_url}/targets" if base_url else "/targets"
    recipients = [user.email for user in users if user.email]
    if not recipients:
        return
    html = (
        "<div style='font-family:Inter,Arial,sans-serif;color:#111827;'>"
        f"<h2 style='margin:0 0 8px;'>{title}</h2>"
        f"<p>{message}</p>"
        f"<p style='margin:20px 0;'><a href='{link}' style='background:#6846DC;color:#fff;"
        "padding:10px 18px;border-radius:6px;text-decoration:none;'>Avaa Kohteet</a></p>"
        "<p style='color:#6B7280;font-size:12px;'>Saat tämän viestin, koska organisaatiollasi on "
        "FlowLeadsin päiväkooste päällä. Koosteen voi kytkeä pois Kohteet-sivulta.</p>"
        "</div>"
    )
    send_system_message(
        Message(
            subject=f"FlowLeads: {title} ({organization.name})",
            recipients=recipients,
            html=html,
            body=f"{message}\n\nAvaa Kohteet: {link}",
        )
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        from datetime import timezone

        return value.replace(tzinfo=timezone.utc)
    return value


# --- saved searches ---------------------------------------------------------------

SAVED_SEARCH_KEYS = ("search", "profile_id", *[key for key in FILTER_KEYS])
MAX_SAVED_SEARCHES = 15


class SavedSearchError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def clean_search_params(raw: dict) -> dict:
    """Only the known /targets parameters, non-empty, as short strings."""
    params = {}
    for key in SAVED_SEARCH_KEYS:
        value = str(raw.get(key) or "").strip()
        if value:
            params[key] = value[:120]
    return params


def list_saved_searches(organization_id: int, user_id: int) -> list[SavedTargetSearch]:
    return (
        SavedTargetSearch.query.filter_by(organization_id=organization_id, user_id=user_id)
        .order_by(SavedTargetSearch.name.asc())
        .all()
    )


def save_search(organization_id: int, user_id: int, name: str, raw_params: dict, *, now: datetime | None = None):
    name = " ".join((name or "").split())[:80]
    params = clean_search_params(raw_params)
    if not name:
        raise SavedSearchError("Anna haulle nimi.")
    if not {k for k in params if k != "sort"}:
        raise SavedSearchError("Valitse ensin vähintään yksi rajaus, jonka haluat tallentaa.")
    existing = SavedTargetSearch.query.filter_by(
        organization_id=organization_id, user_id=user_id, name=name
    ).first()
    if existing is not None:  # same name = update the saved filters
        existing.params = params
        db.session.flush()
        return existing
    if SavedTargetSearch.query.filter_by(organization_id=organization_id, user_id=user_id).count() >= MAX_SAVED_SEARCHES:
        raise SavedSearchError(f"Tallennettuja hakuja voi olla enintään {MAX_SAVED_SEARCHES}. Poista jokin vanha.")
    saved = SavedTargetSearch(
        organization_id=organization_id,
        user_id=user_id,
        name=name,
        params=params,
        notify=True,
        last_notified_at=now or _now(),  # only targets that arrive from now on are "new"
    )
    db.session.add(saved)
    db.session.flush()
    return saved


def get_saved_search(organization_id: int, user_id: int, search_id: int) -> SavedTargetSearch | None:
    return SavedTargetSearch.query.filter_by(
        id=search_id, organization_id=organization_id, user_id=user_id
    ).first()


def _matches_for_search(saved: SavedTargetSearch, since: datetime) -> int:
    """New targets for a saved search since `since`."""
    params = saved.params or {}
    try:
        profile_id = int(params["profile_id"]) if params.get("profile_id") else None
    except (TypeError, ValueError):
        profile_id = None
    query = ListingMatch.query.join(Listing, Listing.id == ListingMatch.listing_id).filter(
        ListingMatch.id.in_(
            _visible_match_ids(saved.organization_id, "pending", profile_id=profile_id)
        ),
        ListingMatch.created_at > since,
    )
    term = " ".join((params.get("search") or "").split())
    if term:
        like = f"%{term}%"
        query = query.filter(
            or_(
                Listing.taloyhtio.ilike(like),
                Listing.osoite.ilike(like),
                Listing.kaupunki.ilike(like),
                Listing.isannoitsija.ilike(like),
            )
        )
    return _apply_target_filters(query, params).count()


def notify_saved_searches(*, now: datetime | None = None) -> int:
    """In-app notification to the owner of each saved search that new targets
    match. Runs right after matching, so it is as immediate as the data."""
    from app.notifications.services import NotificationService
    from app.users.models import User

    from app.billing.services import targets_locked

    now = now or _now()
    sent = 0
    locked: dict[int, bool] = {}
    for saved in SavedTargetSearch.query.filter_by(notify=True).all():
        if saved.organization_id not in locked:
            locked[saved.organization_id] = targets_locked(saved.organization_id)
        if locked[saved.organization_id]:
            continue
        since = _aware(saved.last_notified_at) or _aware(saved.created_at) or now
        count = _matches_for_search(saved, since)
        if not count:
            continue
        user = db.session.get(User, saved.user_id)
        if user is None or not user.is_active:
            continue
        # encoded: city names have umlauts and spaces, a search term may hold "&"
        query = urlencode(
            [(key, value) for key, value in (saved.params or {}).items() if value not in (None, "", [])], doseq=True
        )
        message = (
            "1 uusi kohde vastaa tallennettua hakuasi."
            if count == 1
            else f"{count} uutta kohdetta vastaa tallennettua hakuasi."
        )
        NotificationService.create(
            user_id=user.id,
            organization_id=saved.organization_id,
            type="saved_search_match",
            title=f"Uusia kohteita: {saved.name}",
            message=message,
            link="/targets" + (f"?{query}" if query else ""),
        )
        saved.last_notified_at = now
        sent += 1
    if sent:
        db.session.commit()
    return sent


def top_target_rows(organization_id: int, limit: int = 5) -> list[dict]:
    """The best targets for the dashboard, as Kohteet rows (fit order)."""
    from app.listings.fit_services import learn_preferences, score_matches

    page = list_targets(organization_id, page=1, per_page=limit)
    wanted_types = organization_wanted_types(organization_id)
    fits = score_matches(page.items, learn_preferences(organization_id, wanted_types))
    return [target_row(match, wanted_types, fits.get(match.id)) for match in page.items]
