"""Ventilation cleaning and attic insulation are read from the listing text
(app/listings/text_types.py) and can be chosen as urakat like any other type."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.extensions import db
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.models import Listing, ListingRenovation
from app.listings.text_types import (
    ATTIC_INSULATION,
    VENTILATION,
    backfill_text_types,
    find_text_types,
)
from app.search.constants import REMONTTITYYPIT
from app.search.models import SearchProfile
from app.users.services import create_organization

NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize(
    "text, timing",
    [
        ("Tulevat remontit: 2026 lukitusuudistus, 2027 ilmanvaihtokanavien puhdistus ja vesikaton kunnostus, 2028 parvekkeiden kuntokartoitus.", "2027"),
        ("2028-2030: IV-hormien nuohous ja ilmamäärien säätö, lämmönjakokeskuksen uusiminen.", "2028-2030"),
        ("2027: lämmönjakokeskuksen uusinta sekä lämmityksen tasapainotus ja säätö (samana vuonna myös IV-kanavien puhdistus ja säätö).", "2027"),
        ("Tulevat remontit: 2027 valokatteiden uusiminen, 2028 ilmanvaihtojärjestelmän puhdistus ja säätö.", "2028"),
        ("Ilmanvaihdon mittaus ja tasapainotus suunnitteilla.", ""),
        # renewing the units belongs to the same type (decided 16.11.2026)
        ("Tulevat remontit: 2028 ilmanvaihtokoneiden uusiminen (järjestelmä alkuperäinen), 2028-2029 hissien kunnostus.", "2028"),
        ("Tulevat remontit: ilmanvaihtokoneiden uusiminen 2027-2030.", "2027-2030"),
        ("Tulevat remontit: 2028 ilmanvaihtokoneiden peruskorjaus tai uusiminen.", "2028"),
        ("Ilmanvaihto: tulevat remontit 2027 ilmanvaihtojärjestelmän uusiminen (koneellinen poisto).", "2027"),
        ("Kunnossapitotarveselvitys 2026-2030: ilmanvaihtokoneiden uusimisen suunnittelu 2026, toteutus 2027-2028", "2026-2030"),
    ],
)
def test_ventilation_cleaning_is_found_with_its_year(text, timing):
    found = find_text_types(text)
    assert found[VENTILATION]["timing"] == timing


@pytest.mark.parametrize(
    "text",
    [
        "2027 hissien uusiminen sekä uima-allashuoneen ilmanvaihdon parantaminen käynnissä.",
        "Sähkösaneeraus 2026-2027; lisäksi ilmanvaihtoon ja saunaosastoon liittyviä töitä.",
        "2027 ilmanvaihdon tarkastaminen ja tarvittavat kunnossapitotoimenpiteet.",
        "2027: lämmönjakokeskuksen uusinta sekä lämmityksen tasapainotus ja säätö.",
        "Yläpohjan ja vesikaton tarkastus suunniteltu 2026.",
        "Linjasaneeraus 2027, saunaosaston ilmanvaihtotyö.",
    ],
)
def test_other_work_is_not_mistaken_for_the_new_types(text):
    assert find_text_types(text) == {}


def test_attic_insulation_is_found():
    found = find_text_types("2026 kattojen uusiminen ja yläpohjan lisäeristys (osin jo tehty), 2027 julkisivujen kunnostus.")
    assert found[ATTIC_INSULATION]["timing"] == "2026"
    assert VENTILATION not in found


def _profile(slug: str, rtype: str):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    profile = SearchProfile(
        organization_id=org.id, name="P", remonttityyppi=rtype, remonttityypit=[rtype], regions=["Uusimaa"],
        source="oikotie", is_active=True, min_confidence=60, needs_rematch=False,
    )
    db.session.add(profile)
    db.session.commit()
    return org


def _listing(ext: int) -> Listing:
    listing = Listing(
        source="import", external_id=str(ext), maakunta="Uusimaa", kaupunki="Espoo", osoite=f"Testitie {ext}",
        taloyhtio=f"As Oy Kohde {ext}", analysis_status="pending", first_seen_at=NOW, last_seen_at=NOW,
    )
    db.session.add(listing)
    db.session.commit()
    return listing


def test_new_types_are_selectable_and_never_split_by_the_import():
    assert VENTILATION in REMONTTITYYPIT and ATTIC_INSULATION in REMONTTITYYPIT
    assert REMONTTITYYPIT[-1] == "Muu remontti"
    assert not any(ch in name for name in REMONTTITYYPIT for ch in ",;|")


def test_saved_analysis_adds_the_type_and_matches_the_customer(app):
    with app.app_context():
        org = _profile("iv", VENTILATION)
        listing = _listing(1)
        svc.save_analysis(
            listing.id,
            {
                "status": "done",
                "tulevat_remontit": "2027 linjasaneeraus, 2028 ilmanvaihtokanavien puhdistus ja säätö.",
                "renovations": [{"remonttityyppi": "Putkiremontti", "confidence": 90, "timing": "2027", "evidence": "linjasaneeraus 2027"}],
            },
            now=NOW,
        )
        by_type = {r.remonttityyppi: r for r in db.session.get(Listing, listing.id).renovations}
        assert set(by_type) == {"Putkiremontti", VENTILATION}
        assert by_type[VENTILATION].timing_year == 2028 and by_type[VENTILATION].confidence >= 60
        assert targets.count_targets(org.id) == 1
        row = targets.target_rows(org.id, targets.list_targets(org.id).items, {VENTILATION})[0]
        assert [r.remonttityyppi for r in row["renovations"]] == [VENTILATION]


def test_analyzers_own_classification_is_kept(app):
    with app.app_context():
        listing = _listing(2)
        svc.save_analysis(
            listing.id,
            {
                "status": "done",
                "tulevat_remontit": "Ilmanvaihtokanavien puhdistus 2029.",
                "renovations": [{"remonttityyppi": VENTILATION, "confidence": 95, "timing": "2029", "evidence": "x"}],
            },
            now=NOW,
        )
        rows = ListingRenovation.query.filter_by(listing_id=listing.id, remonttityyppi=VENTILATION).all()
        assert len(rows) == 1 and rows[0].confidence == 95


def test_backfill_reads_listings_analysed_earlier(app):
    with app.app_context():
        org = _profile("yp", ATTIC_INSULATION)
        listing = _listing(3)
        listing.analysis_status = "done"
        listing.tulevat_remontit = "Katto 2027"
        db.session.add(
            ListingRenovation(
                listing_id=listing.id, remonttityyppi="Katto- ja vesikattoremontti", confidence=60, timing="2027",
                evidence="2026 kattojen uusiminen ja yläpohjan lisäeristys, 2027 vesikattoremontti.",
            )
        )
        other = _listing(4)
        other.analysis_status = "done"
        other.tulevat_remontit = "Yläpohjan tarkastus 2026."
        db.session.commit()

        assert backfill_text_types() == 1
        assert backfill_text_types() == 0  # nothing left to add
        assert {r.remonttityyppi for r in db.session.get(Listing, listing.id).renovations} == {
            "Katto- ja vesikattoremontti",
            ATTIC_INSULATION,
        }
        assert targets.count_targets(org.id) == 1
