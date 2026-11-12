"""Customer onboarding without regions, the richer file import, the Kohteet
page changes (own urakat only, year filter, mixed batches) and leads named
after the housing company."""

from __future__ import annotations

import random
from unittest.mock import patch

from app.admin.invite_services import accept_invite, create_invite
from app.admin.onboarding_services import create_customer
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.listings import target_services as targets
from app.listings.import_services import import_listings, parse_listing_csv
from app.listings.models import Listing, ListingMatch
from app.listings.services import rematch_profiles
from app.search.constants import ALL_FINLAND_REGION
from app.search.models import SearchProfile
from app.users.services import create_user

PIPE = "Putkiremontti"
HEAT = "Lämmitysjärjestelmän remontti"
ROOF = "Katto- ja vesikattoremontti"
PASSWORD = "temporarypassword1"


def _superadmin(slug):
    actor = create_user(f"sa-{slug}@test.com", PASSWORD, role="superadmin")
    actor.totp_enabled = True
    db.session.commit()
    return actor


def _customer(slug, types):
    result = create_customer(
        actor=_superadmin(slug),
        organization_name=f"Asiakas {slug}",
        admin_email=f"admin-{slug}@asiakas.fi",
        admin_password=PASSWORD,
        remonttityypit=types,
    )
    result.admin_user.totp_enabled = True
    result.admin_user.must_change_password = False
    db.session.commit()
    return result


CSV_HEADER = (
    "taloyhtio;osoite;kaupunki;maakunta;remonttityyppi;ajankohta;varmuus;teksti;isannoitsija;"
    "isannoitsija_puh;isannoitsija_email;talotyyppi;url;tunniste;yhteyshenkilo;isannointitoimisto;"
    "yhteystiedon_taso;taloyhtion_ytunnus;ilmoitus_poistunut;huomio"
)


def _csv(*rows):
    return ("﻿" + "\n".join([CSV_HEADER, *rows])).encode("utf-8")


def _row(ext, rtype, *, region="Uusimaa", city="Helsinki", conf=70, timing="2027", removed="ei tarkistettu"):
    return (
        f"As Oy Testi {ext};Testitie {ext};{city};{region};{rtype};{timing};{conf};"
        f"Tulossa {rtype} {timing}.;Hannu Räsänen, Retta Isännöinti Oy;010 123 4567;hannu@retta.fi;Kerrostalo;"
        f"https://example.fi/{ext};t-{ext};Hannu Räsänen;Retta Isännöinti Oy;henkilö;0194158-2;{removed};sisäinen huomio"
    )


def test_onboarding_needs_no_regions_and_takes_several_urakat(app):
    with app.app_context():
        result = _customer("noreg", [HEAT, PIPE])
        profile = result.search_profile
        assert profile.regions == [ALL_FINLAND_REGION]
        assert profile.wanted_types == [PIPE, HEAT]  # catalogue order
        assert profile.name == f"{PIPE}, {HEAT}"
        assert profile.min_confidence == 50
        assert profile.is_active


def test_invite_is_accepted_with_a_password_only(app):
    with app.app_context():
        invite = create_invite(
            actor=_superadmin("inv"),
            organization_name="Kutsuttu Oy",
            admin_email="kutsu@asiakas.fi",
            remonttityypit=[ROOF, PIPE],
        )
        db.session.commit()
        assert invite.invite.types == [PIPE, ROOF]
        with patch("app.email.services.EmailService.send_onboarding_complete_email"):
            accepted = accept_invite(invite.invite, password="brandnewpassword1")
        db.session.commit()
        assert accepted.search_profile.regions == [ALL_FINLAND_REGION]
        assert accepted.search_profile.wanted_types == [PIPE, ROOF]


def test_invite_page_asks_no_regions(app, client):
    with app.app_context():
        result = create_invite(
            actor=_superadmin("invpage"),
            organization_name="Sivu Oy",
            admin_email="sivu@asiakas.fi",
            remonttityypit=[PIPE],
        )
        db.session.commit()
        token = result.token
    html = client.get(f"/invite/{token}").get_data(as_text=True)
    assert 'name="regions"' not in html and "Hakualueet" not in html
    assert "koko Suomesta" in html


def test_file_import_keeps_person_business_id_and_office(app):
    with app.app_context():
        rows = parse_listing_csv(_csv(_row(1, PIPE), _row(2, PIPE, removed="kyllä")))
        result = import_listings(rows)
        assert result["errors"] == [] and result["created"] == 2
        listing = Listing.query.filter_by(external_id="t-1").one()
        assert listing.isannoitsija == "Retta Isännöinti Oy"  # the office, so managers group together
        assert listing.isannoitsija_henkilo == "Hannu Räsänen"
        assert listing.ytunnus == "0194158-2"
        assert listing.is_active
        assert not Listing.query.filter_by(external_id="t-2").one().is_active


def test_every_customer_sees_exactly_its_own_urakat(app):
    with app.app_context():
        pipe = _customer("pipe", [PIPE])
        heat = _customer("heat", [HEAT])
        both = _customer("both", [PIPE, ROOF])
        import_listings(
            parse_listing_csv(
                _csv(
                    _row(1, PIPE),
                    _row(2, HEAT, region="Lappi", city="Rovaniemi"),
                    _row(3, f"{PIPE}, {ROOF}", region="Pirkanmaa", city="Tampere"),
                    _row(4, ROOF, conf=50),
                    _row(5, PIPE, conf=40),  # below every profile's confidence
                )
            )
        )
        rematch_profiles()

        def seen(result):
            ids = targets._visible_match_ids(result.organization.id, "pending", include_reserve=True)
            return sorted(m.listing.external_id for m in ListingMatch.query.filter(ListingMatch.id.in_(ids)))

        assert seen(pipe) == ["t-1", "t-3"]
        assert seen(heat) == ["t-2"]
        assert seen(both) == ["t-1", "t-3", "t-4"]


def test_reimport_with_another_type_drops_the_stale_target(app):
    with app.app_context():
        pipe = _customer("stale", [PIPE])
        import_listings(parse_listing_csv(_csv(_row(1, PIPE))))
        rematch_profiles()
        assert ListingMatch.query.filter_by(organization_id=pipe.organization.id).count() == 1
        import_listings(parse_listing_csv(_csv(_row(1, ROOF))))
        assert ListingMatch.query.filter_by(organization_id=pipe.organization.id).count() == 0


def test_filters_offer_own_urakat_years_and_cities_of_the_region(app):
    with app.app_context():
        both = _customer("filters", [PIPE, HEAT])
        import_listings(
            parse_listing_csv(
                _csv(
                    _row(1, f"{PIPE}, {ROOF}", timing="2026-2027"),
                    _row(2, HEAT, region="Pirkanmaa", city="Tampere", timing="2029"),
                    _row(3, HEAT, city="Espoo", timing=""),
                )
            )
        )
        rematch_profiles()
        org_id = both.organization.id
        for match in ListingMatch.query.filter_by(organization_id=org_id):
            match.released_at = match.created_at
        db.session.commit()

        options = targets.target_filter_options(org_id)
        assert options["renovations"] == [PIPE, HEAT] or options["renovations"] == sorted([PIPE, HEAT])
        assert ROOF not in options["renovations"]
        assert options["years"] == ["2026", "2029"]
        assert targets.target_filter_options(org_id, filters={"region": "Uusimaa"})["cities"] == ["Espoo", "Helsinki"]

        def names(**filters):
            return sorted(m.listing.external_id for m in targets.list_targets(org_id, filters=filters).items)

        assert names(year="2027") == ["t-1"]
        assert names(year="2030") == ["t-1", "t-2"]  # unknown timing is not "by 2030"
        row = targets.target_row(
            ListingMatch.query.join(Listing).filter(Listing.external_id == "t-1").first(),
            targets.organization_wanted_types(org_id),
        )
        assert [r.remonttityyppi for r in row["renovations"]] == [PIPE]


def test_fetch_mixes_the_customers_urakat(app):
    app.config["TARGET_FETCH_ENABLED"] = True
    try:
        with app.app_context():
            both = _customer("mix", [PIPE, HEAT])
            # a file import: every listing has the same timestamp, one type after another
            rows = [_row(i, HEAT) for i in range(1, 7)] + [_row(i, PIPE) for i in range(7, 13)]
            import_listings(parse_listing_csv(_csv(*rows)))
            rematch_profiles()
            targets.fetch_targets(both.organization.id, rng=random.Random(1), ignore_cooldown=True)
            db.session.commit()
            released = ListingMatch.query.filter(
                ListingMatch.organization_id == both.organization.id, ListingMatch.released_at.isnot(None)
            ).all()
            types = {r.remonttityyppi for m in released for r in m.listing.renovations}
            assert types == {PIPE, HEAT}
    finally:
        app.config["TARGET_FETCH_ENABLED"] = False


def test_lead_is_named_after_the_housing_company(app):
    with app.app_context():
        pipe = _customer("leadname", [PIPE])
        import_listings(parse_listing_csv(_csv(_row(1, PIPE))))
        rematch_profiles()
        match = ListingMatch.query.filter_by(organization_id=pipe.organization.id).one()
        targets.transfer_targets(pipe.organization.id, [match.id])
        lead = Lead.query.filter_by(organization_id=pipe.organization.id).one()
        assert lead.company == "As Oy Testi 1"
        assert lead.title == "Isännöitsijä: Retta Isännöinti Oy, Hannu Räsänen"
        assert lead.phone == "010 123 4567"
        assert "Taloyhtiön Y-tunnus: 0194158-2" in lead.notes
        assert "sisäinen huomio" not in (lead.notes or "")


def test_customer_admin_settings_menu_is_short(app, client):
    with app.app_context():
        result = _customer("menu", [PIPE])
        email = result.admin_user.email
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    html = client.get("/settings/team").get_data(as_text=True)
    assert "Käyttäjät" in html and "Urakat ja kohdehaku" in html
    main, _, advanced = html.partition("Lisäasetukset")
    assert "API-avaimet" not in main and "API-avaimet" in advanced
    assert 'name="email"' in html  # the invite form is right there
