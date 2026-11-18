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

# type -> alternatives; a type is found when every pattern of one alternative
# is in the same clause of the folded text
_PATTERNS: dict[str, tuple[tuple[re.Pattern, ...], ...]] = {
    VENTILATION: (
        # cleaning, measuring, adjusting
        (
            re.compile(r"ilmanvaih|\biv[- ]|ilmamaar|poistoilma"),
            re.compile(r"puhdist|nuoho|mittau|saato|saado|tasapainot"),
        ),
        # renewing the ventilation units or system ("ilmanvaihtokoneiden
        # peruskorjaus tai uusiminen"): the verb must follow the ventilation word
        (re.compile(r"(ilmanvaih\w*|\biv-\w+)\s+(uusi|uusinta|peruskorja|saneera)"),),
    ),
    ATTIC_INSULATION: (
        (
            re.compile(r"ylapohj|ullako|ullakk|vinti"),
            re.compile(r"erist"),
        ),
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
            for rtype, alternatives in _PATTERNS.items():
                if rtype in found or not any(all(p.search(folded) for p in alt) for alt in alternatives):
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


# Where each type's backfill continues (listing id), kept in the scheduler
# process. The SQL hints are wider than the patterns ("%IV-%" also matches
# "aktiivi-"), so a hinted listing may stay without the type for good: without
# the cursor the same first `limit` rows were read again every run and later
# listings were never reached. A restart only starts the round from the top.
_BACKFILL_CURSOR: dict[str, int] = {}


def backfill_text_types(*, limit: int = 2000) -> int:
    """Listings analysed before these types existed: add the types their text
    mentions and match them to the profiles. Returns the number of listings
    changed. Each run reads the next `limit` hinted listings per type (in id
    order, wrapping around), so every one is reached."""
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
                Listing.id > _BACKFILL_CURSOR.get(rtype, 0),
            )
            .order_by(Listing.id)
            .limit(limit)
            .all()
        )
        # a short page ends the round: start from the top next time
        _BACKFILL_CURSOR[rtype] = listings[-1].id if len(listings) >= limit else 0
        for listing in listings:
            if add_text_types(listing):
                db.session.flush()
                match_listing(listing)
                changed += 1
    if changed:
        db.session.commit()
    return changed
