"""LVI renovation types (19.11.2026): profiles can pick them, the Analyzer's
results are stored and matched, the old heating name still works, and the
types are also read from the listing's own text."""

from datetime import datetime, timezone

import pytest

from app.extensions import db
from app.listings import services as svc
from app.listings.import_services import _renovation_type
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.listings.pts import RENOVATION_MARKS
from app.listings.text_types import (
    HEATING_DEVICES,
    HEATING_METHOD,
    HEATING_NETWORK,
    WATER_FIXTURES,
    backfill_text_types,
    find_text_types,
)
from app.search.constants import REMONTTITYYPIT
from app.search.profile_services import create_profile
from app.users.services import create_organization

NOW = datetime.now(timezone.utc)
PIPE = "Putkiremontti"


def _listing(ext, renovations=(), status="done", **fields):
    listing = Listing(
        source="oikotie",
        external_id=str(ext),
        maakunta="Uusimaa",
        kaupunki="Espoo",
        osoite=f"Testitie {ext}",
        url=f"https://asunnot.oikotie.fi/myytavat-asunnot/espoo/{ext}",
        analysis_status=status,
        first_seen_at=NOW,
        last_seen_at=NOW,
        published_at=NOW,
        **fields,
    )
    for rtype, evidence in renovations:
        listing.renovations.append(ListingRenovation(remonttityyppi=rtype, confidence=90, evidence=evidence))
    db.session.add(listing)
    db.session.flush()
    return listing


def test_lvi_types_are_offered_and_marked():
    for name in (PIPE, WATER_FIXTURES, HEATING_METHOD, HEATING_DEVICES, HEATING_NETWORK):
        assert name in REMONTTITYYPIT
        assert name in RENOVATION_MARKS
    assert "Lämmitysjärjestelmän remontti" not in REMONTTITYYPIT
    assert set(RENOVATION_MARKS) == set(REMONTTITYYPIT)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Tulevat remontit: 2027 vesikalusteiden uusiminen.", {WATER_FIXTURES}),
        ("2026 huoneistojen hanojen ja WC-istuinten vaihto", {WATER_FIXTURES}),
        ("Suunnitteilla siirtyminen kaukolämmöstä maalämpöön 2028.", {HEATING_METHOD}),
        ("2027 lämmönjakokeskuksen uusiminen", {HEATING_DEVICES}),
        ("Patteriverkoston perussäätö 2026.", {HEATING_NETWORK}),
        ("Lämpöjohtojen vuotojen korjaus suunnitteilla.", {HEATING_NETWORK}),
        ("2027 julkisivujen maalaus, 2028 vesikaton uusiminen.", set()),
        ("Linjasaneeraus 2029.", set()),
    ],
)
def test_lvi_types_are_found_from_text(text, expected):
    found = set(find_text_types(text)) & {WATER_FIXTURES, HEATING_METHOD, HEATING_DEVICES, HEATING_NETWORK}
    assert found == expected


def test_profile_with_lvi_types_gets_matching_targets(app):
    with app.app_context():
        org = create_organization("LVI Oy", "lvi")
        db.session.flush()
        profile = create_profile(
            org.id,
            name="LVI",
            remonttityyppi=WATER_FIXTURES,
            remonttityypit=[WATER_FIXTURES, HEATING_DEVICES, HEATING_NETWORK],
            regions=["Uusimaa"],
            daily_lead_limit=0,
        )
        profile.needs_rematch = False
        listing = _listing(1, status="pending")
        other = _listing(2, status="pending")
        db.session.commit()

        result = svc.save_analysis(
            listing.id,
            {
                "status": "done",
                "renovations": [
                    {"remonttityyppi": HEATING_NETWORK, "confidence": 85, "timing": "2027", "evidence": "linjasäätö 2027"},
                    # an Analyzer workflow that was not re-imported still sends the old name
                    {"remonttityyppi": "Lämmitysjärjestelmän remontti", "confidence": 70},
                ],
            },
        )
        assert result["matches"] == 1
        assert {r.remonttityyppi for r in listing.renovations} == {HEATING_NETWORK, HEATING_METHOD}
        svc.save_analysis(other.id, {"status": "done", "renovations": [{"remonttityyppi": "Julkisivuremontti", "confidence": 90}]})

        matches = ListingMatch.query.filter_by(profile_id=profile.id).all()
        assert [m.listing_id for m in matches] == [listing.id]
        assert matches[0].status == "pending"


def test_older_listings_get_lvi_types_from_text(app):
    with app.app_context():
        old = _listing(
            1,
            [(PIPE, "Linjasaneeraus 2029, samalla vesikalusteiden uusiminen.")],
            tulevat_remontit="2027 lämmönjakokeskuksen uusiminen.",
        )
        db.session.commit()
        assert backfill_text_types() == 1
        types = {r.remonttityyppi for r in old.renovations}
        assert types == {PIPE, WATER_FIXTURES, HEATING_DEVICES}
        assert backfill_text_types() == 0  # nothing new the second time


def test_import_accepts_new_and_renamed_type_names():
    assert _renovation_type("Lämmitysjärjestelmän remontti") == HEATING_METHOD
    assert _renovation_type("lämpöjohtoverkoston vianetsintä") == HEATING_NETWORK
    assert _renovation_type("Vesikalusteiden uusiminen") == WATER_FIXTURES
    assert _renovation_type("lammityslaiteasennus") == HEATING_DEVICES
