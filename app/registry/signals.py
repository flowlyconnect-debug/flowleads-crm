"""Renovation signals from the register data (internal, see __init__).

Three kinds, all estimates and labelled as such:

- permit: an alteration permit (Ryhti "Muu muutostyö" / "Uudelleen
  rakentamiseen verrattava muutostyö") decided in the last 24 months and not
  yet completed. The register does not say what is altered, so the type is
  "Muu remontti".
- heating: oil heating, a candidate for a heating-system change.
- age: the typical renovation interval from completion
  (`app/listings/housing.py::_AGE_HINTS`), within ten years past due to five
  years ahead (25 years past due for 1960-85 concrete element facades). Ryhti's open data has no renovation dates, so an "age" signal
  cannot know whether the work is already done.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.listings.housing import _AGE_HINTS
from app.registry.models import RegistryBuilding, RegistryPermit, RegistrySignal
from app.registry.sync import PERMIT_MONTHS

OPEN_PERMIT_STATES = ("Suunniteltu", "Aloitettu")
OIL_SOURCES = ("Kevyt polttoöljy", "Raskas polttoöljy")
AGE_PAST_YEARS = 10
AGE_AHEAD_YEARS = 5
# 1960-85 concrete element facades are the bulk of facade renovations still to do
CONCRETE_FACADE_PAST_YEARS = 25
CHUNK = 2000

CONFIDENCE = {"permit": 70, "heating": 50, "age": 30, "age_overdue": 25, "age_concrete_facade": 45}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def building_signals(building: RegistryBuilding, permits: list[RegistryPermit], *, today: date) -> list[dict]:
    signals: list[dict] = []
    since = today - timedelta(days=PERMIT_MONTHS * 31)
    open_permits = [
        p
        for p in permits
        if p.action_state in OPEN_PERMIT_STATES and p.decision_date and p.decision_date >= since
    ]
    if open_permits:
        latest = max(open_permits, key=lambda p: p.decision_date)
        signals.append(
            {
                "kind": "permit",
                "renovation_type": "Muu remontti",
                "due_year": latest.decision_date.year,
                "confidence": CONFIDENCE["permit"],
                "reason": (
                    f"Muutostyölupa ({latest.action_type or 'muutostyö'}) myönnetty "
                    f"{latest.decision_date.strftime('%d.%m.%Y')}, tila: {latest.action_state.lower()}."
                ),
            }
        )
    if building.heating_source in OIL_SOURCES:
        signals.append(
            {
                "kind": "heating",
                "renovation_type": "Lämmitysjärjestelmän remontti",
                "due_year": today.year,
                "confidence": CONFIDENCE["heating"],
                "reason": f"Lämmitys öljyllä ({building.heating_source.lower()}): lämmitystavan vaihto on todennäköinen.",
            }
        )
    year = building.completion_year
    if year:
        concrete_facade = (
            building.facade_material == "Betoni" and building.construction_method == "Elementti" and 1960 <= year <= 1985
        )
        for rtype, interval, reason in _AGE_HINTS:
            due = year + interval
            facade = rtype == "Julkisivuremontti" and concrete_facade
            past = CONCRETE_FACADE_PAST_YEARS if facade else AGE_PAST_YEARS
            if not today.year - past <= due <= today.year + AGE_AHEAD_YEARS:
                continue
            confidence = CONFIDENCE["age_overdue"] if due < today.year else CONFIDENCE["age"]
            text = f"Valmistunut {year}. {reason}"
            if facade:
                confidence = CONFIDENCE["age_concrete_facade"]
                text += " Betonielementtijulkisivu 1960–80-luvulta."
            signals.append(
                {
                    "kind": "age",
                    "renovation_type": rtype,
                    "due_year": max(due, today.year),
                    "confidence": confidence,
                    "reason": text,
                }
            )
    return signals


def compute_signals(*, now: datetime | None = None) -> dict:
    """Recompute every building's signals; returns counts by kind."""
    now = now or _now()
    today = now.date()
    counts = {"permit": 0, "heating": 0, "age": 0}
    last_id = 0
    while True:
        buildings = (
            RegistryBuilding.query.filter(RegistryBuilding.id > last_id)
            .order_by(RegistryBuilding.id)
            .limit(CHUNK)
            .all()
        )
        if not buildings:
            break
        last_id = buildings[-1].id
        ids = [b.building_id for b in buildings if b.building_id]
        permits: dict[str, list[RegistryPermit]] = {}
        for permit in RegistryPermit.query.filter(RegistryPermit.building_id.in_(ids)):
            permits.setdefault(permit.building_id, []).append(permit)
        existing: dict[tuple, RegistrySignal] = {
            (s.building_id, s.kind, s.renovation_type): s
            for s in RegistrySignal.query.filter(RegistrySignal.building_id.in_([b.id for b in buildings]))
        }
        wanted: set[tuple] = set()
        for building in buildings:
            for signal in building_signals(building, permits.get(building.building_id, []), today=today):
                key = (building.id, signal["kind"], signal["renovation_type"])
                wanted.add(key)
                counts[signal["kind"]] += 1
                row = existing.get(key)
                if row is None:
                    row = RegistrySignal(building_id=building.id, kind=signal["kind"], renovation_type=signal["renovation_type"])
                    db.session.add(row)
                row.due_year = signal["due_year"]
                row.confidence = signal["confidence"]
                row.reason = signal["reason"]
                row.computed_at = now
        for key, row in existing.items():
            if key not in wanted:
                db.session.delete(row)
        db.session.commit()
    return counts
