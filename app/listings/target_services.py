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
from app.listings.models import Listing, ListingMatch
from app.listings.services import _best_renovation, _now, materialize_match

PER_PAGE = 25
MAX_BULK = 200
# tab on the page -> match status
TABS = {"available": "pending", "hidden": "dismissed"}
_LEAD_STATUSES = ("lead_created", "merged")


def _visible_match_ids(organization_id: int, status: str, *, profile_id: int | None = None):
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
    query = query.order_by(
        func.coalesce(Listing.published_at, Listing.first_seen_at).desc(), Listing.id.desc()
    )
    return query.paginate(page=max(1, page), per_page=per_page, error_out=False)


def target_row(match: ListingMatch) -> dict:
    """What the page shows for one target."""
    listing = match.listing
    renovation = _best_renovation(match.profile, listing) if match.profile else None
    if renovation is None and listing.renovations:
        # the profile's filter changed and the match has not been rebuilt yet
        renovation = max(listing.renovations, key=lambda r: r.confidence)
    return {
        "match": match,
        "listing": listing,
        "title": listing.taloyhtio or listing.osoite or listing.external_id,
        "location": ", ".join(
            p for p in ((listing.osoite if listing.taloyhtio else None), listing.kaupunki or listing.maakunta) if p
        ),
        "renovation": renovation,
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


def transfer_targets(organization_id: int, match_ids, *, now: datetime | None = None) -> dict:
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
