"""What is coming at a housing company: every source's observations combined
into one renovation outlook (HousingRenovation, one row per type).

Phase 2 of the shared pipeline (identify -> forecast -> categorise).

Observations:
- a renovation a listing mentions (type, stage, year, confidence, quote);
- a renovation a listing says is already done ("putket uusittu 2015");
- the register signals of the company's buildings (an alteration permit, oil
  heating, the typical renovation age; app/registry/signals.py).

Rules (per renovation type):
1. Done recently (within the type's typical interval) cancels the estimates
   for that type (age, oil heating). A mention in a listing is never cancelled.
2. An alteration permit makes a planned renovation of a type that needs a
   permit decided, and is then not shown separately as "Muu remontti".
3. The stage is the furthest any observation reached, the year the earliest
   at that stage, the confidence the best one, plus CORROBORATION points
   when listings and registers point to the same renovation.
4. Register estimates already published as catalogue listings
   (Listing.is_estimate) are not counted twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_

from app.extensions import db
from app.listings.housing import _AGE_HINTS, fold
from app.listings.models import HousingCompany, HousingRenovation, Listing
from app.listings.stages import STAGES, renovation_stage

CORROBORATION = 10
MAX_CONFIDENCE = 95
CHUNK = 300
OTHER = "Muu remontti"
# renovations that need a building or action permit: a permit makes them decided
PERMIT_TYPES = {
    "Putkiremontti",
    "Julkisivuremontti",
    "Katto- ja vesikattoremontti",
    "Lämmitysjärjestelmän remontti",
    "Yläpohjan lisäeristys",
}
# how long a done renovation keeps an estimate away (years)
_INTERVALS = {rtype: interval for rtype, interval, _ in _AGE_HINTS}
_INTERVALS.setdefault("Lämmitysjärjestelmän remontti", 25)
_INTERVALS.setdefault("Ilmanvaihdon puhdistus ja säätö", 10)
_INTERVALS.setdefault("Yläpohjan lisäeristys", 30)

# folded word stems that name a renovation type in free text
TYPE_WORDS = {
    "Putkiremontti": ("putk", "linjasaneera", "viemar", "kayttovesi", "vesijohto", "lvi saneera"),
    "Julkisivuremontti": ("julkisivu", "parveke", "ikkun"),
    "Katto- ja vesikattoremontti": ("vesikatto", "katto"),
    "Sähkö- ja tietoliikennejärjestelmät": ("sahko", "nousujohto"),
    "Lämmitysjärjestelmän remontti": ("lammity", "lammonjako", "maalamp", "kaukolamp", "patteri"),
    "Ilmanvaihdon puhdistus ja säätö": ("ilmanvaihto", "iv kone"),
    "Yläpohjan lisäeristys": ("ylapohja",),
    "Lukkoremontti / kulunvalvonta": ("lukko", "kulunvalvon"),
}
_DONE_VERB = re.compile(
    r"\b(tehty|tehtiin|tehdyt|valmistui|valmistunut|valmistuneet|uusittu|uusittiin|uusitut|korjattu|korjattiin|"
    r"saneerattu|saneerattiin|toteutettu|toteutettiin|vaihdettu|vaihdettiin|asennettu|asennettiin|remontoitu)\b"
)
_FUTURE = re.compile(r"\b(tulossa|tuleva|suunnitteilla|suunniteltu|alkaa|alkavat|aloitetaan|pts|edessa|ajankohtainen)\b")
_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")
_CLAUSE = re.compile(r"[.;\n!?]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Observation:
    rtype: str
    stage: str
    year: int | None
    confidence: int
    source: str  # "oikotie", "import", "ryhti", ...
    family: str  # "listing" or "register": independent sources corroborate
    kind: str  # "mention", "permit", "heating", "age"
    text: str


def done_renovations(text: str | None, *, now: datetime | None = None) -> dict[str, int]:
    """{type: latest year done} from free text such as "Putket uusittu 2015
    ja julkisivu korjattu 2019". A clause counts only with a done verb, a past
    year and no word of the future; each type takes the year nearest to it."""
    now = now or _now()
    done: dict[str, int] = {}
    for raw in _CLAUSE.split(text or ""):
        clause = fold(raw)
        if not _DONE_VERB.search(clause) or _FUTURE.search(clause):
            continue
        years = [(m.start(), int(m.group(1))) for m in _YEAR.finditer(clause) if int(m.group(1)) <= now.year]
        if not years:
            continue
        for rtype, words in TYPE_WORDS.items():
            for word in words:
                match = re.search(r"\b" + re.escape(word), clause)
                if match:
                    # the year after the word, else the nearest one before it
                    after = [y for pos, y in years if pos > match.start()]
                    year = after[0] if after else min(years, key=lambda py: abs(py[0] - match.start()))[1]
                    done[rtype] = max(done.get(rtype, 0), year)
                    break
    return done


def _listing_observations(listing: Listing) -> list[Observation]:
    observations = []
    for renovation in listing.renovations:
        observations.append(
            Observation(
                rtype=renovation.remonttityyppi,
                stage=renovation_stage(renovation),
                year=renovation.timing_year,
                confidence=renovation.confidence or 0,
                source=listing.source or "import",
                family="listing",
                kind="mention",
                text=(renovation.evidence or renovation.timing or "")[:300],
            )
        )
    return observations


def _signal_observations(signals) -> list[Observation]:
    return [
        Observation(
            rtype=signal.renovation_type,
            stage="decided" if signal.kind == "permit" else "need",
            year=signal.due_year,
            confidence=signal.confidence,
            source="ryhti",
            family="register",
            kind=signal.kind,
            text=signal.reason[:300],
        )
        for signal in signals
    ]


def outlook(observations: list[Observation], done: dict[str, int], *, now: datetime | None = None) -> list[dict]:
    """The combined outlook rows (dicts) from the observations of one company."""
    now = now or _now()
    by_type: dict[str, list[Observation]] = {}
    for observation in observations:
        by_type.setdefault(observation.rtype, []).append(observation)

    # 1. a recent done renovation cancels the estimates of its type
    for rtype, items in list(by_type.items()):
        year = done.get(rtype)
        if year and now.year - year < _INTERVALS.get(rtype, 30):
            kept = [o for o in items if o.kind == "mention"]
            if kept:
                by_type[rtype] = kept
            else:
                del by_type[rtype]

    # 2. a permit decides the planned renovations that need one
    permits = [o for o in by_type.get(OTHER, []) if o.kind == "permit"]
    if permits:
        permit = max(permits, key=lambda o: o.year or 0)
        explained = False
        for rtype in PERMIT_TYPES & set(by_type):
            items = by_type[rtype]
            planned = [o for o in items if o.kind == "mention" and o.stage == "planned"]
            if planned:
                # the permit decides it; the renovation keeps its planned year (the permit's is the decision's)
                year = min((o.year for o in planned if o.year), default=permit.year)
                items.append(
                    Observation(rtype, "decided", year, permit.confidence, "ryhti", "register", "permit", permit.text)
                )
                explained = True
        if explained:
            rest = [o for o in by_type[OTHER] if o.kind != "permit"]
            if rest:
                by_type[OTHER] = rest
            else:
                del by_type[OTHER]

    rows = []
    for rtype, items in by_type.items():
        stage = min((o.stage for o in items), key=STAGES.index)
        at_stage = [o for o in items if o.stage == stage]
        years = [o.year for o in at_stage if o.year] or [o.year for o in items if o.year]
        confidence = max(o.confidence for o in items)
        families = {o.family for o in items}
        if len(families) > 1:
            confidence += CORROBORATION
        items.sort(key=lambda o: (STAGES.index(o.stage), -o.confidence))
        rows.append(
            {
                "remonttityyppi": rtype,
                "stage": stage,
                "year": min(years) if years else None,
                "confidence": min(MAX_CONFIDENCE, confidence),
                "estimate": families == {"register"},
                "sources": sorted({o.source for o in items}),
                "evidence": [
                    {"source": o.source, "kind": o.kind, "stage": o.stage, "year": o.year, "confidence": o.confidence, "text": o.text}
                    for o in items[:6]
                ],
            }
        )
    rows.sort(key=lambda r: (STAGES.index(r["stage"]), -(r["confidence"])))
    return rows


def _company_inputs(company_ids: list[int]) -> dict[int, tuple[list[Observation], dict[str, int]]]:
    """The observations and done renovations of each company, in a few queries."""
    from sqlalchemy.orm import selectinload

    from app.registry.models import RegistryBuilding, RegistrySignal

    inputs: dict[int, tuple[list[Observation], dict[str, int]]] = {cid: ([], {}) for cid in company_ids}
    listings = (
        Listing.query.filter(Listing.housing_id.in_(company_ids), Listing.is_estimate.is_(False))
        .options(selectinload(Listing.renovations))
        .all()
    )
    for listing in listings:
        observations, done = inputs[listing.housing_id]
        observations.extend(_listing_observations(listing))
        for rtype, year in done_renovations(" ".join(p for p in (listing.tulevat_remontit, listing.description) if p)).items():
            done[rtype] = max(done.get(rtype, 0), year)
    buildings = RegistryBuilding.query.filter(RegistryBuilding.housing_id.in_(company_ids)).all()
    building_company = {b.id: b.housing_id for b in buildings}
    if building_company:
        for signal in RegistrySignal.query.filter(RegistrySignal.building_id.in_(list(building_company))):
            inputs[building_company[signal.building_id]][0].extend(_signal_observations([signal]))
    return inputs


def compute_outlooks(*, company_ids: list[int] | None = None, limit: int = 5000, now: datetime | None = None) -> int:
    """Recompute the outlook of the given companies, or of those that changed
    since their last outlook (new facts, a listing analysed since). Returns
    how many companies were computed."""
    now = now or _now()
    if company_ids is None:
        changed_listing = (
            db.session.query(Listing.housing_id)
            .filter(Listing.housing_id == HousingCompany.id, Listing.analyzed_at > HousingCompany.outlook_at)
            .exists()
        )
        company_ids = [
            cid
            for (cid,) in db.session.query(HousingCompany.id)
            .filter(
                or_(
                    HousingCompany.outlook_at.is_(None),
                    HousingCompany.updated_at > HousingCompany.outlook_at,
                    changed_listing,
                )
            )
            .order_by(HousingCompany.id)
            .limit(limit)
        ]
    done_count = 0
    for start in range(0, len(company_ids), CHUNK):
        chunk = company_ids[start : start + CHUNK]
        inputs = _company_inputs(chunk)
        existing: dict[tuple[int, str], HousingRenovation] = {
            (r.housing_id, r.remonttityyppi): r
            for r in HousingRenovation.query.filter(HousingRenovation.housing_id.in_(chunk))
        }
        wanted: set[tuple[int, str]] = set()
        before = {cid: set() for cid in chunk}
        for cid, rtype in existing:
            before[cid].add(rtype)
        after = {cid: set() for cid in chunk}
        for company_id in chunk:
            observations, done = inputs[company_id]
            for row in outlook(observations, done, now=now):
                key = (company_id, row["remonttityyppi"])
                wanted.add(key)
                after[company_id].add(row["remonttityyppi"])
                record = existing.get(key)
                if record is None:
                    record = HousingRenovation(housing_id=company_id, remonttityyppi=row["remonttityyppi"])
                    db.session.add(record)
                for field in ("stage", "year", "confidence", "estimate", "sources", "evidence"):
                    setattr(record, field, row[field])
                record.computed_at = now
        for key, record in existing.items():
            if key not in wanted:
                db.session.delete(record)
        # wall-clock time: compared with updated_at and analyzed_at, which are too
        HousingCompany.query.filter(HousingCompany.id.in_(chunk)).update(
            {HousingCompany.outlook_at: _now()}, synchronize_session=False
        )
        db.session.commit()
        changed = [cid for cid in chunk if before[cid] != after[cid]]
        if changed:
            _rematch(changed, now)
        done_count += len(chunk)
    return done_count


def _rematch(company_ids: list[int], now: datetime) -> None:
    """The renovation types of these companies changed (a done renovation
    cancelled one, a new source added one): their listings' matches follow."""
    from app.listings.services import match_listing

    listings = Listing.query.filter(Listing.housing_id.in_(company_ids), Listing.analysis_status == "done").all()
    for listing in listings:
        match_listing(listing, now=now)
    db.session.commit()
