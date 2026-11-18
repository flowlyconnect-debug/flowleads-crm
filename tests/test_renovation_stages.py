"""How far a renovation is (decided / planned / need, app/listings/stages.py),
how it weighs in the score, and the Tarve tab that keeps needs apart from
decided and planned renovations on Kohteet."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.housing import classify, score_target
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.listings.stages import classify_stage
from app.search.models import SearchProfile
from app.users.services import create_organization, create_user

NOW = datetime.now(timezone.utc)
PIPE = "Putkiremontti"
PASSWORD = "securepassword1"


@pytest.mark.parametrize(
    "text, stage",
    [
        ("Yhtiökokous on päättänyt putkiremontista, työt alkavat 2027.", "decided"),
        ("Putkiremontti on käynnissä.", "decided"),
        ("Julkisivuremontin urakoitsija on valittu.", "decided"),
        ("Rakennuslupa on myönnetty julkisivuremontille.", "decided"),
        ("Kattoremontti alkaa keväällä 2027.", "decided"),
        ("Hankesuunnittelu on käynnissä, remontti arviolta 2028.", "planned"),
        ("Kuntotutkimuksen laatiminen on käynnissä.", "planned"),
        ("Yhtiökokous hyväksyi hankesuunnittelun aloittamisen.", "planned"),
        ("Yhtiökokous päätti tilata kuntotutkimuksen.", "planned"),
        ("PTS:n mukaan vesikatto uusitaan 2027–2028.", "planned"),
        ("Linjasaneeraus ei ole vielä päätetty, PTS 2029.", "planned"),
        ("Yhtiökokous 15.4.2026 valtuutti hallituksen tilaamaan yhtiön viemäreiden sukituksen.", "decided"),
        ("Yhtiökokous valtuutti hallituksen tilaamaan kuntotutkimuksen.", "planned"),
        ("Ylimääräinen yhtiökokous päätti aloittaa parveke- ja ikkunaremontin suunnittelun.", "planned"),
        ("Hallitus päättänyt edistää märkätilakorjausten suunnittelua 2026.", "planned"),
        ("Taloyhtiö päätti marraskuussa 2025 parveke- ja julkisivuremontista vuosille 2026-2027.", "decided"),
        ("Putkiremontti edessä lähivuosina.", "need"),
        ("Ikkunoiden uusiminen ajankohtainen.", "need"),
        ("Rakennuslupa haetaan keväällä.", "need"),
    ],
)
def test_stage_from_the_text(text, stage):
    assert classify_stage(text, None) == stage


def test_a_year_alone_is_a_plan_and_register_estimates_are_needs():
    assert classify_stage("Putkiremontti", "2028", 2028) == "planned"
    note = " Arvio julkisista rekistereistä (Syke Ryhti, PRH), ei varmistettu tieto."
    assert classify_stage("Valmistunut 1972. Putkiremontti on tyypillisesti ajankohtainen." + note, None) == "need"
    assert classify_stage("Muutostyölupa (Muu muutostyö) myönnetty 01.05.2026, tila: suunniteltu." + note, None) == "decided"


def test_stage_is_stored_on_save(app):
    with app.app_context():
        listing = Listing(source="import", external_id="s1", maakunta="Uusimaa", analysis_status="done")
        listing.renovations.append(
            ListingRenovation(remonttityyppi=PIPE, confidence=80, evidence="Yhtiökokous päätti putkiremontista.")
        )
        db.session.add(listing)
        db.session.commit()
        assert listing.renovations[0].stage == "decided"


def _scored(evidence, timing_year=None, *, phone=True):
    listing = Listing(isannoitsija_puh="040 1" if phone else None, isannoitsija_email="a@b.fi" if phone else None,
                      published_at=NOW, first_seen_at=NOW)
    listing.renovations = [ListingRenovation(remonttityyppi=PIPE, confidence=85, evidence=evidence, timing_year=timing_year)]
    return score_target([listing], {PIPE}, now=NOW)


def test_a_decided_renovation_scores_above_a_planned_one(app):
    decided = _scored("Yhtiökokous päätti putkiremontista.", NOW.year + 1)
    planned = _scored("PTS:ssä putkiremontti.", NOW.year + 1)
    need = _scored("Putkiremontti edessä.")
    assert decided["total"] - planned["total"] == 10
    assert classify(decided) == "A" and classify(planned) == "A"
    assert classify(need) == "T"  # only a need: the Tarve tab, whatever the score


@pytest.fixture
def production(app):
    from app.config import Config  # the production defaults themselves

    app.config.update(TARGETS_VISIBLE_LIMIT=Config.TARGETS_VISIBLE_LIMIT, TARGETS_MIN_TIER=Config.TARGETS_MIN_TIER,
                      TARGETS_NEED_LIMIT=Config.TARGETS_NEED_LIMIT)
    yield
    app.config.update(TARGETS_VISIBLE_LIMIT=0, TARGETS_MIN_TIER="C", TARGETS_NEED_LIMIT=-1)


def _target(n: int, evidence: str, timing_year=None) -> Listing:
    listing = Listing(
        source="import", external_id=f"t{n}", maakunta="Uusimaa", kaupunki="Espoo", osoite=f"Katu {n}",
        taloyhtio=f"As Oy Vaihe {n}", analysis_status="done", first_seen_at=NOW, last_seen_at=NOW,
        published_at=NOW - timedelta(hours=n), isannoitsija="Isännöinti Oy", isannoitsija_puh="040 1",
        isannoitsija_email="a@b.fi",
    )
    db.session.add(listing)
    db.session.flush()
    db.session.add(ListingRenovation(listing_id=listing.id, remonttityyppi=PIPE, confidence=85, evidence=evidence,
                                     timing_year=timing_year, timing=str(timing_year) if timing_year else None))
    db.session.flush()
    svc.match_listing(listing, now=NOW)
    db.session.commit()
    return listing


def test_needs_are_on_the_tarve_tab_not_the_main_list(app, client, production):
    with app.app_context():
        org = create_organization("Org vaihe", "vaihe")
        db.session.flush()
        user = create_user("admin-vaihe@test.com", PASSWORD, role="admin", organization_id=org.id)
        user.totp_enabled = True
        db.session.add(SearchProfile(organization_id=org.id, name="P", remonttityyppi=PIPE, regions=["Uusimaa"],
                                     source="oikotie", is_active=True, min_confidence=50, needs_rematch=False))
        db.session.commit()
        _target(1, "Yhtiökokous päätti putkiremontista, työt alkavat 2027.", NOW.year + 1)
        _target(2, "PTS:ssä putkiremontti.", NOW.year + 3)
        _target(3, "Putkiremontti edessä lähivuosina.")
        tiers = {m.listing.external_id: m.tier for m in ListingMatch.query.filter_by(organization_id=org.id)}
        assert tiers["t3"] == "T" and tiers["t1"] == "A"
        assert targets.count_targets(org.id) == 2
        assert targets.count_targets(org.id, segment="need") == 1
        email = user.email

    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    main = client.get("/targets").get_data(as_text=True)
    main_list, ahead = main.split('class="targets-ahead"')
    assert "As Oy Vaihe 1" in main_list and "As Oy Vaihe 3" not in main_list
    # each row says why it is shown; the decided one says so
    assert f'class="target-row__why">Putkiremontti päätetty, alkaa {NOW.year + 1}' in main_list
    # the need waits in the closed Ennakoi section under the list
    assert "Ennakoi: 1 taloyhtiö," in ahead and "As Oy Vaihe 3" in ahead
    need = client.get("/targets?tab=need").get_data(as_text=True)
    assert "As Oy Vaihe 3" in need and "As Oy Vaihe 1" not in need


def test_without_the_tarve_tab_needs_stay_on_the_main_list(app):
    """TARGETS_NEED_LIMIT -1 (the older tests' setting): a need counts as C."""
    with app.app_context():
        org = create_organization("Org yksi", "yksi")
        db.session.flush()
        db.session.add(SearchProfile(organization_id=org.id, name="P", remonttityyppi=PIPE, regions=["Uusimaa"],
                                     source="oikotie", is_active=True, min_confidence=50, needs_rematch=False))
        db.session.commit()
        _target(4, "Putkiremontti edessä lähivuosina.")
        assert targets.count_targets(org.id) == 1 and targets.count_targets(org.id, segment="need") == 0


def test_the_analyzers_own_stage_wins_over_the_rules(app):
    with app.app_context():
        listing = Listing(source="import", external_id="an1", maakunta="Uusimaa", analysis_status="processing")
        db.session.add(listing)
        db.session.commit()
        svc.save_analysis(
            listing.id,
            {
                "status": "done",
                "renovations": [
                    {"remonttityyppi": PIPE, "confidence": 80, "timing": "2027",
                     "evidence": "Putkiremontti 2027.", "vaihe": "paatetty"},
                    {"remonttityyppi": "Julkisivuremontti", "confidence": 70, "timing": "",
                     "evidence": "Julkisivu edessä."},
                ],
            },
        )
        stages = {r.remonttityyppi: r.stage for r in Listing.query.get(listing.id).renovations}
        assert stages == {PIPE: "decided", "Julkisivuremontti": "need"}
