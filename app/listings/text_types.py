"""Renovation types found from a listing's own "tulevat remontit" text.

The two types below are small jobs that the original eight types (and the
import files made before 16.11.2026) folded into "Muu remontti" or mentioned
only inside another renovation's evidence. They are picked up from the text:
the listing's summary of coming renovations and the evidence of its other
renovations, both of which describe work that is still to come.

Runs when an analysis is saved (imports and the Analyzer) and, for listings
stored earlier, in the `listing_matching` scheduler job (`backfill_text_types`).
A type the listing already has is never added twice.
"""

from __future__ import annotations

import re
import unicodedata

from app.extensions import db
from app.listings.models import Listing, ListingRenovation

VENTILATION = "Ilmanvaihdon puhdistus ja säätö"
ATTIC_INSULATION = "Yläpohjan lisäeristys"
TEXT_TYPE_CONFIDENCE = 75

# type -> (every pattern must be found in the same clause of the folded text)
_PATTERNS: dict[str, tuple[re.Pattern, ...]] = {
    VENTILATION: (
        re.compile(r"ilmanvaih|\biv[- ]|ilmamaar|poistoilma"),
        re.compile(r"puhdist|nuoho|mittau|saato|saado|tasapainot"),
    ),
    ATTIC_INSULATION: (
        re.compile(r"ylapohj|ullako|ullakk|vinti"),
        re.compile(r"erist"),
    ),
}
# SQL prefilter for the backfill (ILIKE, so accents as written)
_SQL_HINTS: dict[str, tuple[str, ...]] = {
    VENTILATION: ("%ilmanvaih%", "%IV-%", "%ilmamäär%", "%poistoilma%"),
    ATTIC_INSULATION: ("%yläpohj%", "%ullak%", "%vint%"),
}
_YEARS_RE = re.compile(r"(?<!\d)(20\d{2})(?:\s*[-–]\s*(20\d{2}))?(?!\d)")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\s*[|\n]\s*")
_CLAUSE_RE = re.compile(r"[,;()]|\bsekä\b")


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def _timing(text: str) -> str:
    match = _YEARS_RE.search(text)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}" if match.group(2) else match.group(1)


def find_text_types(text: str | None) -> dict[str, dict]:
    """{type: {"timing", "evidence"}} for the types the text mentions. The
    year is the one in the same clause ("2027 ilmanvaihtokanavien puhdistus"),
    else the sentence's first one."""
    found: dict[str, dict] = {}
    for sentence in _SENTENCE_RE.split(text or ""):
        sentence = sentence.strip()
        if not sentence:
            continue
        for clause in _CLAUSE_RE.split(sentence):
            folded = _fold(clause)
            for rtype, patterns in _PATTERNS.items():
                if rtype in found or not all(p.search(folded) for p in patterns):
                    continue
                found[rtype] = {
                    "timing": _timing(clause) or _timing(sentence),
                    "evidence": sentence[:2000],
                }
    return found


def add_text_types(listing: Listing) -> list[ListingRenovation]:
    """Add the text-found types the listing does not have yet. No commit."""
    from app.listings.services import _parse_year

    have = {r.remonttityyppi for r in listing.renovations}
    texts = [listing.tulevat_remontit or ""] + [r.evidence or "" for r in listing.renovations]
    added = []
    for text in texts:
        for rtype, hit in find_text_types(text).items():
            if rtype in have:
                continue
            have.add(rtype)
            renovation = ListingRenovation(
                remonttityyppi=rtype,
                confidence=TEXT_TYPE_CONFIDENCE,
                timing=hit["timing"] or None,
                timing_year=_parse_year(hit["timing"], low=2000),
                evidence=hit["evidence"],
            )
            listing.renovations.append(renovation)
            added.append(renovation)
    return added


def backfill_text_types(*, limit: int = 2000) -> int:
    """Listings analysed before these types existed: add the types their text
    mentions and match them to the profiles. Returns the number of listings
    changed. Cheap to repeat: only listings whose text hints at a type they do
    not have are read."""
    from sqlalchemy import or_

    from app.listings.services import match_listing

    changed = 0
    for rtype, hints in _SQL_HINTS.items():
        has_type = db.session.query(ListingRenovation.listing_id).filter(ListingRenovation.remonttityyppi == rtype)
        in_evidence = db.session.query(ListingRenovation.listing_id).filter(
            or_(*[ListingRenovation.evidence.ilike(hint) for hint in hints])
        )
        listings = (
            Listing.query.filter(
                Listing.is_active.is_(True),
                Listing.analysis_status == "done",
                Listing.id.notin_(has_type),
                or_(*[Listing.tulevat_remontit.ilike(hint) for hint in hints], Listing.id.in_(in_evidence)),
            )
            .limit(limit)
            .all()
        )
        for listing in listings:
            if add_text_types(listing):
                db.session.flush()
                match_listing(listing)
                changed += 1
    if changed:
        db.session.commit()
    return changed
