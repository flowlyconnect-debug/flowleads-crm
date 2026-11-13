"""Sopivuus: how well a target suits the organization, 0-100, with the reasons.

A simple, explainable score, not a model:

- the listing itself (up to 90): how certain the wanted renovation is, how soon
  it starts and whether the manager's phone and email are known;
- what the organization has done before (up to +-25): the renovation types,
  regions and building decades of the targets it moved to leads count up, the
  ones it hid count down. With no history the bonus is zero, so the score
  learns as the customer uses the Kohteet page.

Used by the Kohteet page: the default sort "Sopivimmat ensin" and the score
chip on each row. Read only; nothing is stored.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.extensions import db
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.listings.services import _now

_TRANSFERRED = ("lead_created", "merged")
_DISMISSED = ("dismissed",)
# share of the history bonus per feature
_WEIGHTS = {"type": 15.0, "region": 7.0, "decade": 3.0}
_MIN_EVIDENCE = 2  # a feature value needs this many decisions before it counts in the reasons


def _decade(year: int | None) -> int | None:
    return (int(year) // 10) * 10 if year else None


@dataclass
class Preferences:
    """Per feature value: (moved to leads, hidden) counts."""

    transferred: dict[str, Counter] = field(default_factory=lambda: {k: Counter() for k in _WEIGHTS})
    dismissed: dict[str, Counter] = field(default_factory=lambda: {k: Counter() for k in _WEIGHTS})
    decisions: int = 0  # listings moved or hidden so far

    def lean(self, feature: str, value) -> float:
        """-1 (always hidden) .. +1 (always moved), smoothed towards 0."""
        if value in (None, ""):
            return 0.0
        moved = self.transferred[feature][value]
        hidden = self.dismissed[feature][value]
        return (moved - hidden) / (moved + hidden + 2)

    def evidence(self, feature: str, value) -> int:
        return self.transferred[feature][value] + self.dismissed[feature][value]


def learn_preferences(organization_id: int, wanted_types: set[str] | None = None) -> Preferences:
    """Count the organization's earlier decisions on the Kohteet page."""
    prefs = Preferences()
    rows = (
        db.session.query(ListingMatch.status, Listing.id, Listing.maakunta, Listing.build_year)
        .join(Listing, Listing.id == ListingMatch.listing_id)
        .filter(
            ListingMatch.organization_id == organization_id,
            ListingMatch.status.in_(_TRANSFERRED + _DISMISSED),
        )
        .all()
    )
    if not rows:
        return prefs
    decided: dict[int, tuple[str, str | None, int | None]] = {}
    for status, listing_id, region, build_year in rows:
        # one decision per listing; moving it to a lead wins over a hidden duplicate
        kind = "transferred" if status in _TRANSFERRED else "dismissed"
        if decided.get(listing_id, ("",))[0] != "transferred":
            decided[listing_id] = (kind, region, build_year)
    types_by_listing: dict[int, set[str]] = {}
    for listing_id, rtype in db.session.query(ListingRenovation.listing_id, ListingRenovation.remonttityyppi).filter(
        ListingRenovation.listing_id.in_(list(decided))
    ):
        if not wanted_types or rtype in wanted_types:
            types_by_listing.setdefault(listing_id, set()).add(rtype)
    prefs.decisions = len(decided)
    for listing_id, (kind, region, build_year) in decided.items():
        bucket = getattr(prefs, kind)
        if region:
            bucket["region"][region] += 1
        decade = _decade(build_year)
        if decade:
            bucket["decade"][decade] += 1
        for rtype in types_by_listing.get(listing_id, ()):
            bucket["type"][rtype] += 1
    return prefs


def _timing_points(year: int | None, this_year: int) -> tuple[float, str | None]:
    if year is None:
        return 10.0, None
    if year <= this_year + 1:
        return 25.0, f"Remontti {year}"
    if year <= this_year + 3:
        return 15.0, f"Remontti {year}"
    return 6.0, None


def score_target(listing: Listing, renovation: ListingRenovation | None, prefs: Preferences) -> tuple[int, list[str]]:
    """0-100 and the reasons worth showing, most important first."""
    this_year = _now().year
    reasons: list[str] = []
    points = 0.0

    if renovation is not None:
        points += 40.0 * max(0, min(100, renovation.confidence or 0)) / 100
        timing, label = _timing_points(renovation.timing_year, this_year)
        points += timing
        if label:
            reasons.append(label)
    has_phone = bool((listing.isannoitsija_puh or "").strip())
    has_email = bool((listing.isannoitsija_email or "").strip())
    if has_phone and has_email:
        points += 20.0
        reasons.append("Puhelin ja sähköposti")
    elif has_phone or has_email:
        points += 13.0
        reasons.append("Puhelin" if has_phone else "Sähköposti")
    if listing.build_year:
        points += 5.0

    if prefs.decisions:
        rtype = renovation.remonttityyppi if renovation is not None else None
        features = (("type", rtype), ("region", listing.maakunta), ("decade", _decade(listing.build_year)))
        for feature, value in features:
            lean = prefs.lean(feature, value)
            points += _WEIGHTS[feature] * lean
            if value and prefs.evidence(feature, value) >= _MIN_EVIDENCE and abs(lean) >= 0.3:
                label = f"{value}-luku" if feature == "decade" else str(value)
                reasons.insert(0, ("Viet usein liideiksi: " if lean > 0 else "Piilotat usein: ") + label)

    return max(0, min(100, round(points))), reasons


class ListPage:
    """The parts of Flask-SQLAlchemy's Pagination the Kohteet page uses, for a
    list sorted in Python."""

    def __init__(self, items: list, page: int, per_page: int):
        self.total = len(items)
        self.per_page = per_page
        self.pages = max(1, -(-self.total // per_page)) if self.total else 0
        self.page = max(1, page)
        start = (self.page - 1) * per_page
        self.items = items[start : start + per_page]
        self.has_prev = self.page > 1
        self.has_next = self.page < self.pages
        self.prev_num = self.page - 1 if self.has_prev else None
        self.next_num = self.page + 1 if self.has_next else None


def target_renovation(match: ListingMatch) -> ListingRenovation | None:
    """The renovation a target is about (as on its row on the Kohteet page)."""
    from app.listings.services import _best_renovation

    listing = match.listing
    renovation = _best_renovation(match.profile, listing) if match.profile else None
    if renovation is None and listing.renovations:
        renovation = max(listing.renovations, key=lambda r: r.confidence)
    return renovation


def learning_bonus(listing: Listing, renovation: ListingRenovation | None, prefs: Preferences) -> tuple[float, list[str]]:
    """The +-25 points (and reasons) from the organization's earlier decisions."""
    points = 0.0
    reasons: list[str] = []
    if not prefs.decisions:
        return points, reasons
    rtype = renovation.remonttityyppi if renovation is not None else None
    features = (("type", rtype), ("region", listing.maakunta), ("decade", _decade(listing.build_year)))
    for feature, value in features:
        lean = prefs.lean(feature, value)
        points += _WEIGHTS[feature] * lean
        if value and prefs.evidence(feature, value) >= _MIN_EVIDENCE and abs(lean) >= 0.3:
            label = f"{value}-luku" if feature == "decade" else str(value)
            reasons.append(("Viet usein liideiksi: " if lean > 0 else "Piilotat usein: ") + label)
    return points, reasons


def company_fit(company: list, renovation: ListingRenovation | None, prefs: Preferences, wanted_types=None) -> dict:
    """Sopivuus of a housing company: its target score (app/listings/housing.py,
    all its listings: certainty, timing, contacts, signals, freshness) scaled to
    90, plus the learned +-25. Returns {"fit", "reasons", "parts", "learned"}."""
    from app.listings.housing import score_target as housing_score

    types = set(wanted_types or ())
    if types and renovation is not None:
        types.add(renovation.remonttityyppi)  # the renovation the row shows always counts
    base = housing_score(company, types or None)
    learned, learned_reasons = learning_bonus(company[0], renovation, prefs)
    reasons = list(learned_reasons)
    parts = {p["key"]: p for p in base["parts"]}
    if parts["timing"]["points"] >= 20:
        reasons.append("Remontti " + parts["timing"]["detail"].replace("alkaa ", ""))
    contact = parts["contact"]["detail"]
    reasons.append({"puhelin ja sähköposti": "Puhelin ja sähköposti", "puhelin": "Puhelin", "sähköposti": "Sähköposti"}.get(contact, ""))
    if len(company) > 1:
        reasons.append(f"{len(company)} ilmoitusta samasta taloyhtiöstä")
    fit = max(0, min(100, round(0.9 * base["total"] + learned)))
    return {"fit": fit, "reasons": [r for r in reasons if r], "parts": base["parts"], "learned": round(learned), "base": base["total"]}


def score_matches(matches, prefs: Preferences, *, company_listings: dict | None = None, wanted_types=None) -> dict[int, tuple[int, list[str]]]:
    """{match id: (sopivuus, reasons)}. The housing companies' listings are
    looked up when not given (the organization's visible ones)."""
    from app.listings.housing import company_group, org_company_listings

    matches = list(matches)
    if not matches:
        return {}
    if company_listings is None:
        company_listings = org_company_listings(
            matches[0].organization_id, {company_group(m.listing) for m in matches}
        )
    result = {}
    for m in matches:
        company = list(company_listings.get(company_group(m.listing)) or [])
        if all(other.id != m.listing.id for other in company):
            company.insert(0, m.listing)
        info = company_fit(company, target_renovation(m), prefs, wanted_types)
        result[m.id] = (info["fit"], info["reasons"])
    return result
