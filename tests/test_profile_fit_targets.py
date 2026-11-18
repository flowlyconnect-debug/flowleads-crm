"""Kohteet shows what fits the customer's profile, not a fixed number of the
best: every housing company whose wanted renovation is decided or planned and
starts within the profile's window is visible, each row says why, needs wait in
the closed Ennakoi section, and the window moves with the year."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import Config
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.search.constants import DEFAULT_TIMING_YEARS
from app.search.models import SearchProfile
from app.users.services import create_organization, create_user

NOW = datetime.now(timezone.utc)
PIPE = "Putkiremontti"
FACADE = "Julkisivuremontti"
PASSWORD = "securepassword1"


@pytest.fixture
def production(app):
    app.config.update(TARGETS_VISIBLE_LIMIT=Config.TARGETS_VISIBLE_LIMIT, TARGETS_MIN_TIER=Config.TARGETS_MIN_TIER,
                      TARGETS_NEED_LIMIT=Config.TARGETS_NEED_LIMIT)
    yield
    app.config.update(TARGETS_VISIBLE_LIMIT=0, TARGETS_MIN_TIER="C", TARGETS_NEED_LIMIT=-1)


def test_the_defaults_show_everything_that_fits():
    assert Config.TARGETS_VISIBLE_LIMIT == 0  # no fixed cap
    assert Config.TARGETS_MIN_TIER == "C"  # every decided or planned renovation
    assert Config.TARGETS_NEED_LIMIT == 0  # Ennakoi on, without a cap
    assert DEFAULT_TIMING_YEARS == 2  # this year and the next two


def _org(slug: str, *, timing=DEFAULT_TIMING_YEARS):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    user.totp_enabled = True
    db.session.add(SearchProfile(organization_id=org.id, name="P", remonttityyppi=PIPE, regions=["Uusimaa"],
                                 source="oikotie", is_active=True, min_confidence=50, needs_rematch=False,
                                 timing_within_years=timing))
    db.session.commit()
    return org, user


def _target(n: int, evidence: str, timing_year=None, *, confidence=85, contact=True) -> Listing:
    listing = Listing(
        source="import", external_id=f"f{n}", maakunta="Uusimaa", kaupunki="Espoo", osoite=f"Katu {n}",
        taloyhtio=f"As Oy Sopiva {n}", analysis_status="done", first_seen_at=NOW, last_seen_at=NOW,
        published_at=NOW - timedelta(days=200), isannoitsija="Isännöinti Oy",
        isannoitsija_puh="040 1" if contact else None, isannoitsija_email="a@b.fi" if contact else None,
    )
    db.session.add(listing)
    db.session.flush()
    db.session.add(ListingRenovation(listing_id=listing.id, remonttityyppi=PIPE, confidence=confidence, evidence=evidence,
                                     timing_year=timing_year, timing=str(timing_year) if timing_year else None))
    db.session.flush()
    svc.match_listing(listing, now=NOW)
    db.session.commit()
    return listing


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def test_a_weak_but_fitting_target_is_shown(app, production):
    """A planned renovation with an unsure text, no contact and an old listing is
    a C, and still on the list: it fits the profile."""
    with app.app_context():
        org, _ = _org("heikko")
        _target(1, "PTS:ssä putkiremontti.", NOW.year + 2, confidence=55, contact=False)
        match = ListingMatch.query.filter_by(organization_id=org.id).one()
        assert match.tier == "C"
        assert targets.count_targets(org.id) == 1


def test_many_fitting_targets_are_all_shown(app, production):
    with app.app_context():
        org, _ = _org("monta")
        for n in range(30):
            _target(n, "Yhtiökokous päätti putkiremontista.", NOW.year + 1)
        assert targets.count_targets(org.id) == 30
        assert targets.waiting_summary(org.id) == {"queued": 0, "below": 0}


def test_the_window_keeps_far_renovations_off_the_list(app, production):
    with app.app_context():
        org, _ = _org("ikkuna")
        _target(1, "Yhtiökokous päätti putkiremontista.", NOW.year + DEFAULT_TIMING_YEARS)
        _target(2, "Yhtiökokous päätti putkiremontista.", NOW.year + DEFAULT_TIMING_YEARS + 1)
        _target(3, "Yhtiökokous päätti putkiremontista.")  # no year known: never excluded
        shown = {m.listing.external_id for m in ListingMatch.query.filter_by(organization_id=org.id)}
        assert shown == {"f1", "f3"}


def test_the_window_moves_with_the_year(app):
    with app.app_context():
        org, _ = _org("vuosi")
        profile = SearchProfile.query.filter_by(organization_id=org.id).one()
        profile.updated_at = NOW.replace(year=NOW.year - 1)
        db.session.commit()
        assert svc.roll_timing_windows(now=NOW) == 1
        assert db.session.get(SearchProfile, profile.id).needs_rematch is True
        # rematched this year: nothing to do until the next one
        profile = db.session.get(SearchProfile, profile.id)
        profile.needs_rematch = False
        db.session.commit()
        assert svc.roll_timing_windows(now=NOW) == 0


def test_why_line_says_why_in_plain_words():
    contact = SimpleNamespace(isannoitsija_puh="040 1", isannoitsija_email="a@b.fi")
    decided = SimpleNamespace(remonttityyppi=PIPE, stage="decided", timing_year=2027, is_estimate=False)
    planned = SimpleNamespace(remonttityyppi=FACADE, stage="planned", timing_year=None, is_estimate=False)
    assert targets.why_line([decided, planned], contact) == (
        "Putkiremontti päätetty, alkaa 2027 + 1 muu urakkasi · isännöitsijän puhelin ja sähköposti tiedossa"
    )
    # the decided one leads even when listed after a need
    need = SimpleNamespace(remonttityyppi=FACADE, stage="need", timing_year=2027, is_estimate=False, confidence=90)
    assert targets.why_line([need, decided], contact).startswith("Putkiremontti päätetty, alkaa 2027 + 1 muu urakkasi")
    estimate = SimpleNamespace(remonttityyppi=PIPE, stage="need", timing_year=2028, is_estimate=True)
    no_contact = SimpleNamespace(isannoitsija_puh=None, isannoitsija_email=None)
    assert targets.why_line([estimate], no_contact) == "Putkiremontti tarve, arviolta 2028 (arvio julkisista rekistereistä)"
    assert targets.why_line([planned], SimpleNamespace(isannoitsija_puh="", isannoitsija_email="a@b.fi")) == (
        "Julkisivuremontti suunnitteilla, aikataulu auki · isännöitsijän sähköposti tiedossa"
    )


def test_kohteet_explains_the_rule_and_keeps_needs_in_ennakoi(app, client, production):
    with app.app_context():
        org, user = _org("sivu")
        _target(1, "Yhtiökokous päätti putkiremontista.", NOW.year + 1)
        _target(2, "Putkiremontti edessä lähivuosina.")
        email = user.email
    _login(client, email)
    html = client.get("/targets").get_data(as_text=True)
    assert "1 taloyhtiö sopii profiiliisi." in html
    assert f"putkiremontti\n        on päätetty tai suunniteltu ja alkaa viimeistään {NOW.year + DEFAULT_TIMING_YEARS}" in html
    main_list, ahead = html.split('class="targets-ahead"')
    assert "As Oy Sopiva 1" in main_list and "As Oy Sopiva 2" not in main_list
    assert "Ennakoi: 1 taloyhtiö, joilla urakkasi remontti on vasta tarve" in ahead
    assert "Putkiremontti tarve, aikataulu auki" in ahead
    # the less used filters wait under one closed section
    assert '<details class="targets-more">' in html and "Kerro mitä etsit" in html
    # no tab of its own for needs any more; the full Ennakoi list still opens
    assert ">Tarve <" not in html
    need = client.get("/targets?tab=need").get_data(as_text=True)
    assert "As Oy Sopiva 2" in need and "Takaisin kohteisiin" in need


def test_onboarding_gives_the_default_window(app):
    from app.admin.onboarding_services import create_customer

    with app.app_context():
        actor = create_user("sa-ikkuna@test.com", PASSWORD, role="superadmin")
        db.session.commit()
        result = create_customer(
            actor=actor, organization_name="Asiakas ikkuna", admin_email="admin-ikkuna@asiakas.fi",
            admin_password="temporarypassword1", admin_name=None, search_profile_name="Profiili",
            regions=["Uusimaa"], remonttityyppi=PIPE,
        )
        assert result.search_profile.timing_within_years == DEFAULT_TIMING_YEARS


def test_the_window_switch_turns_the_window_off(app, production):
    with app.app_context():
        app.config["PROFILE_TIMING_WINDOW_ENABLED"] = False
        try:
            org, _ = _org("kytkin")
            _target(1, "Yhtiökokous päätti putkiremontista.", NOW.year + DEFAULT_TIMING_YEARS + 3)
            assert targets.count_targets(org.id) == 1
        finally:
            app.config["PROFILE_TIMING_WINDOW_ENABLED"] = True
