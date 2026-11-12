"""Kohteet: the catalogue listings an organization's search profiles match and
that are not leads yet.

A target is a `ListingMatch` in status "pending". Nothing becomes a lead on its
own: a user moves targets to leads here (`transfer_targets`) or hides the ones
they do not want (`dismiss_targets`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import contains_eager, joinedload

from app.extensions import db
from app.listings.models import Listing, ListingMatch, ListingRenovation, SavedTargetSearch
from app.listings.services import _best_renovation, _now, materialize_match

PER_PAGE = 25
MAX_BULK = 200
# tab on the page -> match status
TABS = {"available": "pending", "hidden": "dismissed"}
_LEAD_STATUSES = ("lead_created", "merged")


def fetch_enabled() -> bool:
    from flask import current_app

    return bool(current_app.config.get("TARGET_FETCH_ENABLED"))


def _visible_match_ids(
    organization_id: int,
    status: str,
    *,
    profile_id: int | None = None,
    include_reserve: bool = False,
):
    """One match id per listing: a listing matched by several of the
    organization's profiles is one target, and a listing that already became a
    lead through another profile is not a target at all."""
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
        )
        .group_by(ListingMatch.listing_id)
    )
    if status == "pending" and fetch_enabled() and not include_reserve:
        # only what the customer has released with "Hae kohteita" (a release
        # time in the future is still inside its progress animation)
        query = query.filter(
            ListingMatch.released_at.isnot(None), ListingMatch.released_at <= _now()
        )
    if status == "pending":
        # hidden once = hidden for the organization, even when another of its
        # profiles matches the same listing later
        hidden = db.session.query(ListingMatch.listing_id).filter(
            ListingMatch.organization_id == organization_id,
            ListingMatch.status == "dismissed",
        )
        query = query.filter(ListingMatch.listing_id.notin_(hidden))
    if profile_id:
        query = query.filter(ListingMatch.profile_id == profile_id)
    return query


def count_targets(organization_id: int, status: str = "pending") -> int:
    return _visible_match_ids(organization_id, status).count()


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
    if sort == "oldest":
        query = query.order_by(newest.asc(), Listing.id.asc())
    elif sort == "city":
        query = query.order_by(func.lower(func.coalesce(Listing.kaupunki, "")).asc(), newest.desc())
    elif sort == "confidence":
        query = query.order_by(ListingMatch.confidence.desc(), newest.desc())
    else:
        query = query.order_by(newest.desc(), Listing.id.desc())
    return query.paginate(page=max(1, page), per_page=per_page, error_out=False)


# Query-string keys of the Kohteet filter bar (kept across actions and pages).
FILTER_KEYS = ("renovation", "region", "city", "year", "building", "contact", "sort")
SORT_OPTIONS = (
    ("", "Uusimmat ensin"),
    ("oldest", "Vanhimmat ensin"),
    ("confidence", "Varmimmat ensin"),
    ("city", "Kaupungin mukaan"),
)


def _apply_target_filters(query, filters: dict):
    """Narrow the target list. Every filter is optional; unknown values simply
    match nothing, they never raise."""
    renovation = (filters.get("renovation") or "").strip()
    if renovation:
        query = query.filter(
            Listing.id.in_(
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
            Listing.id.in_(
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
        query = query.filter(
            or_(
                func.length(func.trim(func.coalesce(Listing.isannoitsija_email, ""))) > 0,
                func.length(func.trim(func.coalesce(Listing.isannoitsija_puh, ""))) > 0,
            )
        )
    return query


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
    return {
        "renovations": sorted(renovations),
        "cities": distinct(Listing.kaupunki, *([Listing.maakunta == region] if region else [])),
        "regions": distinct(Listing.maakunta),
        "buildings": distinct(Listing.building_type),
        "years": [str(y) for y in sorted(years)],
    }


def target_row(match: ListingMatch, wanted_types: set[str] | None = None) -> dict:
    """What the page shows for one target."""
    listing = match.listing
    renovation = _best_renovation(match.profile, listing) if match.profile else None
    if renovation is None and listing.renovations:
        # the profile's filter changed and the match has not been rebuilt yet
        renovation = max(listing.renovations, key=lambda r: r.confidence)
    # every renovation on the listing the organization does, best first
    renovations = sorted(
        (r for r in listing.renovations if not wanted_types or r.remonttityyppi in wanted_types),
        key=lambda r: (r is not renovation, -r.confidence),
    ) or ([renovation] if renovation else [])
    return {
        "match": match,
        "listing": listing,
        "title": listing.taloyhtio or listing.osoite or listing.external_id,
        "location": ", ".join(
            p for p in ((listing.osoite if listing.taloyhtio else None), listing.kaupunki or listing.maakunta) if p
        ),
        "renovation": renovation,
        "renovations": renovations,
        "seen_at": listing.published_at or listing.first_seen_at,
    }


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
    several profiles)."""
    return ListingMatch.query.filter(
        ListingMatch.organization_id == match.organization_id,
        ListingMatch.listing_id == match.listing_id,
        ListingMatch.id != match.id,
        ListingMatch.status.in_(("pending", "dismissed")),
    ).all()


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
        if lead.id not in result["lead_ids"]:
            result["lead_ids"].append(lead.id)
        match = db.session.get(ListingMatch, match_id)
        for sibling in _siblings(match):
            sibling.status = "merged"
            sibling.lead_id = lead.id
            sibling.lead_created_at = now
        db.session.commit()
    return result


def _set_status(organization_id: int, match_ids, *, from_status: str, to_status: str) -> int:
    changed = 0
    for match in _own_matches(organization_id, match_ids, (from_status,)):
        match.status = to_status
        changed += 1
        for sibling in _siblings(match):
            if sibling.status == from_status:
                sibling.status = to_status
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
    from app.tasks.settings import get_organization_settings
    from app.users.models import Organization, User

    now = now or _now()
    notified = 0
    for organization in Organization.query.filter_by(is_active=True).all():
        settings = get_organization_settings(organization.id)
        if not settings.targets_digest_enabled:
            continue
        since = settings.targets_digest_sent_at or (now - timedelta(days=1))
        if since.tzinfo is None:
            since = since.replace(tzinfo=now.tzinfo)
        reserve_mode = fetch_enabled()
        if reserve_mode:
            new_count = (
                db.session.query(func.count(ListingMatch.id))
                .filter(
                    ListingMatch.listing_id.in_(_reserve_listing_ids(organization.id) or [-1]),
                    ListingMatch.organization_id == organization.id,
                    ListingMatch.created_at > since,
                )
                .scalar()
                or 0
            )
        else:
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
        if reserve_mode:
            title = "Uusia kohteita haettavissa"
            message = (
                "Hakuprofiiliisi sopivia uusia kohteita on tarjolla. Avaa Kohteet ja paina "
                "Hae kohteita."
            )
        else:
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


# --- "Hae kohteita": the customer releases a batch from the reserve --------------


class TargetFetchCooldown(Exception):
    """The organization already fetched within the cooldown."""

    def __init__(self, next_fetch_at: datetime):
        super().__init__("cooldown")
        self.next_fetch_at = next_fetch_at


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        from datetime import timezone

        return value.replace(tzinfo=timezone.utc)
    return value


def _reserve_listing_ids(organization_id: int) -> list[int]:
    """Listings waiting in the reserve (no released match yet), newest first."""
    released = db.session.query(ListingMatch.listing_id).filter(
        ListingMatch.organization_id == organization_id,
        ListingMatch.status == "pending",
        ListingMatch.released_at.isnot(None),
    )
    rows = (
        db.session.query(Listing.id)
        .join(ListingMatch, ListingMatch.listing_id == Listing.id)
        .filter(
            ListingMatch.id.in_(_visible_match_ids(organization_id, "pending", include_reserve=True)),
            ListingMatch.released_at.is_(None),
            Listing.id.notin_(released),
        )
        .order_by(func.coalesce(Listing.published_at, Listing.first_seen_at).desc(), Listing.id.desc())
        .all()
    )
    return [listing_id for (listing_id,) in rows]


def _interleave_by_type(organization_id: int, listing_ids: list[int]) -> list[int]:
    """Newest first, but taking turns between the organization's renovation
    types, so a customer with several urakat gets a mix in every batch (a
    file import gives hundreds of listings the same timestamp)."""
    if len(listing_ids) < 2:
        return listing_ids
    wanted = organization_wanted_types(organization_id)
    best: dict[int, tuple[int, str]] = {}
    rows = db.session.query(
        ListingRenovation.listing_id, ListingRenovation.remonttityyppi, ListingRenovation.confidence
    ).filter(ListingRenovation.listing_id.in_(listing_ids))
    for listing_id, rtype, confidence in rows.all():
        if wanted and rtype not in wanted:
            continue
        if listing_id not in best or confidence > best[listing_id][0]:
            best[listing_id] = (confidence, rtype)
    queues: dict[str, list[int]] = {}
    for listing_id in listing_ids:
        queues.setdefault(best.get(listing_id, (0, ""))[1], []).append(listing_id)
    if len(queues) < 2:
        return listing_ids
    ordered: list[int] = []
    lanes = list(queues.values())
    while lanes:
        for lane in lanes:
            ordered.append(lane.pop(0))
        lanes = [lane for lane in lanes if lane]
    return ordered


def count_reserve(organization_id: int) -> int:
    return len(_reserve_listing_ids(organization_id)) if fetch_enabled() else 0


def next_fetch_time(organization_id: int, *, now: datetime | None = None) -> datetime | None:
    """When the organization may fetch again; None = right now."""
    from datetime import timedelta

    from flask import current_app

    from app.tasks.settings import get_organization_settings

    now = now or _now()
    last = _aware(get_organization_settings(organization_id).targets_fetched_at)
    if last is None:
        return None
    available = last + timedelta(minutes=current_app.config.get("TARGET_FETCH_COOLDOWN_MINUTES", 60))
    return available if available > now else None


def fetch_targets(
    organization_id: int,
    *,
    now: datetime | None = None,
    rng=None,
    ignore_cooldown: bool = False,
) -> dict:
    """Release a batch of reserve targets to the Kohteet page.

    The batch size varies from press to press (TARGET_FETCH_MIN..MAX, capped
    by what the reserve holds). The targets become visible `wait_seconds`
    from now, when the page's progress animation ends, so a reload in between
    shows nothing early. One fetch per cooldown for the organization, also
    when nothing was found. Raises TargetFetchCooldown."""
    import random
    from datetime import timedelta

    from flask import current_app

    from app.tasks.settings import get_organization_settings

    now = now or _now()
    rng = rng or random
    config = current_app.config
    if not ignore_cooldown:
        waiting_until = next_fetch_time(organization_id, now=now)
        if waiting_until is not None:
            raise TargetFetchCooldown(waiting_until)

    low = max(1, int(config.get("TARGET_FETCH_MIN", 3)))
    high = max(low, int(config.get("TARGET_FETCH_MAX", 8)))
    wait_low, wait_high = config.get("TARGET_FETCH_WAIT_SECONDS", (10, 15))
    wait_seconds = rng.randint(int(wait_low), int(wait_high))

    reserve = _interleave_by_type(organization_id, _reserve_listing_ids(organization_id))
    chosen = reserve[: min(len(reserve), rng.randint(low, high))]
    if chosen:
        # every match of the organization for these listings: a listing that
        # matched two profiles is one target
        ListingMatch.query.filter(
            ListingMatch.organization_id == organization_id,
            ListingMatch.listing_id.in_(chosen),
            ListingMatch.status == "pending",
            ListingMatch.released_at.is_(None),
        ).update({"released_at": now + timedelta(seconds=wait_seconds)}, synchronize_session=False)

    settings = get_organization_settings(organization_id)
    settings.targets_fetched_at = now
    db.session.flush()
    return {
        "count": len(chosen),
        "wait_seconds": wait_seconds,
        "next_fetch_at": now + timedelta(minutes=config.get("TARGET_FETCH_COOLDOWN_MINUTES", 60)),
    }


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
    """New targets for a saved search since `since`: released ones and, with
    the reserve on, ones that can be fetched."""
    params = saved.params or {}
    try:
        profile_id = int(params["profile_id"]) if params.get("profile_id") else None
    except (TypeError, ValueError):
        profile_id = None
    query = ListingMatch.query.join(Listing, Listing.id == ListingMatch.listing_id).filter(
        ListingMatch.id.in_(
            _visible_match_ids(saved.organization_id, "pending", profile_id=profile_id, include_reserve=True)
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

    now = now or _now()
    sent = 0
    for saved in SavedTargetSearch.query.filter_by(notify=True).all():
        since = _aware(saved.last_notified_at) or _aware(saved.created_at) or now
        count = _matches_for_search(saved, since)
        if not count:
            continue
        user = db.session.get(User, saved.user_id)
        if user is None or not user.is_active:
            continue
        query = "&".join(f"{key}={value}" for key, value in (saved.params or {}).items())
        if fetch_enabled():
            message = "Tallennettua hakuasi vastaavia uusia kohteita on haettavissa. Avaa Kohteet ja paina Hae kohteita."
        else:
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
