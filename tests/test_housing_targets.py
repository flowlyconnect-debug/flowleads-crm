"""Phase 3: Kohteet groups and scores by the taloyhtiö across sources and its
combined outlook (housing_registry + forecast)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.extensions import db
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.forecast import compute_outlooks
from app.listings.housing import refresh_scores
from app.listings.housing_registry import resolve_listings, resolve_registry
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.registry.models import RegistryBuilding, RegistrySignal
from app.search.models import SearchProfile
from app.users.services import create_organization

NOW = datetime.now(timezone.utc)
PIPE = "Putkiremontti"
FACADE = "Julkisivuremontti"


@pytest.fixture
def org(app):
    with app.app_context():
        organization = create_organization("Org yhtiö", "yhtio")  # every module, estimates included
        db.session.flush()
        db.session.add(SearchProfile(
            organization_id=organization.id, name="P", remonttityyppi=PIPE, remonttityypit=[PIPE, FACADE],
            regions=["Pirkanmaa"], source="oikotie", is_active=True, min_confidence=50, needs_rematch=False,
        ))
        db.session.commit()
        yield organization.id


def _building_with_signal(rtype=PIPE, kind="age", confidence=30):
    building = RegistryBuilding(
        building_key="b-yh", building_type="Kerrostalo", municipality="Tampere", address="Koivukuja 5",
        address_keys=["koivukuja5"], completion_year=1975, addresses_fetched_at=NOW,
    )
    db.session.add(building)
    db.session.flush()
    db.session.add(RegistrySignal(building_id=building.id, kind=kind, renovation_type=rtype, due_year=NOW.year,
                                  confidence=confidence, reason="Valmistunut 1975. Arvio julkisista rekistereistä, ei varmistettu tieto."))
    db.session.commit()
    return building


def _estimate_listing(rtype=PIPE):
    listing = Listing(source="ryhti", external_id="b-yh", maakunta="Pirkanmaa", kaupunki="Tampere", osoite="Koivukuja 5",
                      analysis_status="done", is_estimate=True, first_seen_at=NOW, published_at=NOW)
    listing.renovations.append(ListingRenovation(remonttityyppi=rtype, confidence=30, timing_year=NOW.year,
                                                 evidence="Valmistunut 1975. Arvio julkisista rekistereistä, ei varmistettu tieto."))
    db.session.add(listing)
    db.session.flush()
    svc.match_listing(listing, now=NOW)
    db.session.commit()
    return listing


def _oikotie_listing(**renovation):
    listing = Listing(source="oikotie", external_id="o-yh", maakunta="Pirkanmaa", kaupunki="Tampere",
                      osoite="Koivukuja 5 B 12", taloyhtio="As Oy Koivukuja 5", analysis_status="done",
                      first_seen_at=NOW, published_at=NOW, isannoitsija="Isännöinti Oy", isannoitsija_puh="040 1",
                      description=renovation.pop("description", None))
    if renovation:
        listing.renovations.append(ListingRenovation(**renovation))
    db.session.add(listing)
    db.session.flush()
    svc.match_listing(listing, now=NOW)
    db.session.commit()
    return listing


def _pipeline():
    resolve_registry()
    resolve_listings()
    compute_outlooks()
    refresh_scores()


def test_a_listing_and_a_register_estimate_of_one_taloyhtio_are_one_target(app, org):
    with app.app_context():
        _building_with_signal()
        _estimate_listing()
        _oikotie_listing(remonttityyppi=PIPE, confidence=70, timing="2028", timing_year=2028, evidence="PTS:ssä 2028.")
        assert targets.count_targets(org) == 2  # before: two keys (name vs nothing)
        _pipeline()
        keys = {l.company_key for l in Listing.query}
        assert len(keys) == 1 and keys.pop().startswith("h:")
        assert targets.count_targets(org) == 1
        row = targets.target_rows(org, targets.list_targets(org, segment=None).items, {PIPE, FACADE})[0]
        pipe = next(r for r in row["renovations"] if r.remonttityyppi == PIPE)
        assert pipe.sources == ["oikotie", "ryhti"] and pipe.confidence == 80  # 70 + 10 for two sources
        assert pipe.stage == "planned" and not pipe.is_estimate


def test_a_done_renovation_takes_the_estimate_target_away(app, org):
    with app.app_context():
        _building_with_signal()
        estimate = _estimate_listing()
        assert ListingMatch.query.filter_by(listing_id=estimate.id, status="pending").count() == 1
        _oikotie_listing(remonttityyppi=FACADE, confidence=80, timing="2027", timing_year=2027,
                         evidence="Julkisivuremontti 2027.", description="Putket uusittu 2016.")
        _pipeline()
        assert ListingMatch.query.filter_by(listing_id=estimate.id, status="pending").count() == 0
        row = targets.target_rows(org, targets.list_targets(org, segment=None).items, {PIPE, FACADE})[0]
        assert [r.remonttityyppi for r in row["renovations"]] == [FACADE]


def test_the_outlook_raises_the_stored_score(app, org):
    with app.app_context():
        _building_with_signal()
        _estimate_listing()
        listing = _oikotie_listing(remonttityyppi=PIPE, confidence=70, timing="2028", timing_year=2028, evidence="PTS:ssä 2028.")
        before = ListingMatch.query.filter_by(listing_id=listing.id).one().score
        _pipeline()
        after = ListingMatch.query.filter_by(listing_id=listing.id).one().score
        assert after > before  # the corroborated confidence counts


def test_search_and_title_use_the_whole_company(app, org):
    """The row may stand for a nameless register building: the company's name
    still finds it and is its title."""
    with app.app_context():
        _building_with_signal()
        _estimate_listing()
        _oikotie_listing(remonttityyppi=PIPE, confidence=70, timing="2028", timing_year=2028, evidence="PTS:ssä 2028.")
        _pipeline()
        found = targets.list_targets(org, search="As Oy Koivukuja", segment=None).items
        assert len(found) == 1
        row = targets.target_rows(org, found, {PIPE, FACADE})[0]
        assert row["title"] == "As Oy Koivukuja 5"


def test_an_estimate_year_does_not_speed_up_a_decided_renovation(app):
    from types import SimpleNamespace

    from app.listings.housing import score_target

    decided = SimpleNamespace(remonttityyppi=PIPE, confidence=80, timing_year=NOW.year + 4, timing=str(NOW.year + 4),
                              stage="decided", evidence=None)
    estimate = SimpleNamespace(remonttityyppi=FACADE, confidence=30, timing_year=NOW.year, timing=str(NOW.year),
                               stage="need", evidence=None)
    listing = SimpleNamespace(renovations=[decided, estimate], isannoitsija_puh=None, isannoitsija_email=None,
                              isannoitsija=None, published_at=NOW, first_seen_at=NOW, housing_id=None)
    parts = {p["key"]: p for p in score_target([listing], None, now=NOW)["parts"]}
    assert parts["timing"]["detail"] == f"alkaa {NOW.year + 4}"
