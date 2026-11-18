"""The renovation outlook of a housing company, combined from every source
(app/listings/forecast.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.listings.forecast import Observation, compute_outlooks, done_renovations, outlook
from app.listings.housing_registry import Candidate, resolve
from app.listings.models import HousingCompany, HousingRenovation, Listing, ListingRenovation
from app.registry.models import RegistryBuilding, RegistrySignal

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)
PIPE = "Putkiremontti"
FACADE = "Julkisivuremontti"


def _mention(rtype, stage, year=None, confidence=80, source="oikotie"):
    return Observation(rtype, stage, year, confidence, source, "listing", "mention", f"{rtype} {stage}")


def _register(rtype, kind, year=None, confidence=30):
    stage = "decided" if kind == "permit" else "need"
    return Observation(rtype, stage, year, confidence, "ryhti", "register", kind, f"{kind} {rtype}")


def test_done_renovations_are_read_from_the_text():
    text = "Putket uusittu 2015 ja julkisivu korjattu 2019. Vesikatto uusitaan 2028. Ikkunat vaihdettu."
    assert done_renovations(text, now=NOW) == {PIPE: 2015, FACADE: 2019}


def test_a_recent_done_renovation_cancels_the_estimate():
    rows = outlook([_register(PIPE, "age", 2026)], {PIPE: 2015}, now=NOW)
    assert rows == []
    # long ago: due again
    assert [r["remonttityyppi"] for r in outlook([_register(PIPE, "age", 2026)], {PIPE: 1970}, now=NOW)] == [PIPE]


def test_a_mention_is_never_cancelled_by_a_done_note():
    rows = outlook([_mention(PIPE, "planned", 2028)], {PIPE: 2015}, now=NOW)
    assert rows[0]["stage"] == "planned"


def test_listing_and_register_together_raise_the_confidence():
    rows = outlook([_mention(PIPE, "planned", 2028, confidence=70), _register(PIPE, "age", 2026)], {}, now=NOW)
    row = rows[0]
    assert row["stage"] == "planned" and row["year"] == 2028
    assert row["confidence"] == 80 and row["estimate"] is False
    assert row["sources"] == ["oikotie", "ryhti"]


def test_a_permit_decides_a_planned_renovation_that_needs_one():
    rows = outlook([_mention(FACADE, "planned", 2027), _register("Muu remontti", "permit", 2026, confidence=70)], {}, now=NOW)
    assert [(r["remonttityyppi"], r["stage"]) for r in rows] == [(FACADE, "decided")]


def test_a_permit_alone_stays_other_renovation():
    rows = outlook([_register("Muu remontti", "permit", 2026, confidence=70)], {}, now=NOW)
    assert rows[0]["remonttityyppi"] == "Muu remontti" and rows[0]["stage"] == "decided" and rows[0]["estimate"]


def test_compute_outlooks_combines_a_company_s_sources(app):
    with app.app_context():
        company = resolve(Candidate(source="prh", name="As Oy Ennuste", business_id="5555555-5", municipality="Tampere"))
        building = RegistryBuilding(building_key="b-f", building_type="Kerrostalo", municipality="Tampere",
                                    completion_year=1975, housing_id=company.id)
        db.session.add(building)
        db.session.flush()
        db.session.add_all([
            RegistrySignal(building_id=building.id, kind="age", renovation_type=PIPE, due_year=2026, confidence=25, reason="ikä"),
            RegistrySignal(building_id=building.id, kind="age", renovation_type=FACADE, due_year=2026, confidence=30, reason="ikä"),
        ])
        listing = Listing(source="oikotie", external_id="f1", maakunta="Pirkanmaa", analysis_status="done",
                          housing_id=company.id, description="Julkisivu korjattu 2020.")
        listing.renovations.append(ListingRenovation(remonttityyppi=PIPE, confidence=75, timing="2028", timing_year=2028,
                                                     evidence="Linjasaneeraus PTS:ssä 2028."))
        db.session.add(listing)
        # an estimate listing of the same building is not counted twice
        db.session.add(Listing(source="ryhti", external_id="b-f", maakunta="Pirkanmaa", analysis_status="done",
                               is_estimate=True, housing_id=company.id))
        db.session.commit()

        assert compute_outlooks(now=NOW) == 1
        rows = {r.remonttityyppi: r for r in HousingRenovation.query.filter_by(housing_id=company.id)}
        assert set(rows) == {PIPE}  # the facade estimate is cancelled: done in 2020
        assert rows[PIPE].stage == "planned" and rows[PIPE].confidence == 85 and rows[PIPE].year == 2028
        assert db.session.get(HousingCompany, company.id).outlook_at is not None

        # nothing changed: nothing to compute
        assert compute_outlooks(now=NOW) == 0
