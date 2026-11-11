"""Profile filters over the listing catalogue, one lead per housing company,
retention + health, and the onboarding rules that go with them (forced
password change, optional modules per organization)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.admin.onboarding_services import create_customer
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.listings import services as svc
from app.listings.models import CrawlState, Listing, ListingMatch, ListingRenovation
from app.search.profile_services import (
    SearchProfileServiceError,
    create_profile,
    update_profile,
)
from app.users.models import AuditLog, Organization, User
from app.users.services import create_organization, create_user

# Automatic match -> lead pipeline; the default manual flow is in test_targets.py.
pytestmark = pytest.mark.usefixtures("auto_create_leads")

LOCK = "Lukitus, kulunvalvonta ja oviautomatiikka"
PIPE = "Putki- ja viemäriurakointi"
NOW =datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def _org(slug):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    return org


def _profile(org, **fields):
    data = dict(name="P", remonttityyppi=LOCK, regions=["Uusimaa"], daily_lead_limit=0)
    data.update(fields)
    profile = create_profile(org.id, **data)
    profile.needs_rematch = False
    db.session.flush()
    return profile


def _listing(ext, *, renovations=((LOCK, 90, None),), **fields):
    data = dict(
        source="oikotie",
        external_id=str(ext),
        maakunta="Uusimaa",
        kaupunki="Espoo",
        osoite=f"Testitie {ext}",
        url=f"https://asunnot.oikotie.fi/myytavat-asunnot/espoo/{ext}",
        analysis_status="done",
        first_seen_at=NOW,
        last_seen_at=NOW,
        published_at=NOW,
    )
    data.update(fields)
    listing = Listing(**data)
    for rtype, confidence, year in renovations:
        listing.renovations.append(
            ListingRenovation(remonttityyppi=rtype, confidence=confidence, timing_year=year)
        )
    db.session.add(listing)
    db.session.flush()
    return listing


def _wants(profile, listing):
    return svc._profile_wants(profile, listing, now=NOW)


# --- profile filters ---------------------------------------------------------


def test_profile_can_want_several_renovation_types(app):
    with app.app_context():
        org = _org("multi")
        profile = _profile(org, remonttityypit=[PIPE, LOCK])
        assert profile.remonttityyppi == LOCK  # the primary one stays first
        assert profile.wanted_types == [LOCK, PIPE]

        pipe_only = _listing(1, renovations=[(PIPE, 80, None)])
        both = _listing(2, renovations=[(PIPE, 70, None), (LOCK, 95, None)])
        facade = _listing(3, renovations=[("Julkisivu- ja parvekeurakointi", 99, None)])

        assert _wants(profile, pipe_only).remonttityyppi == PIPE
        assert _wants(profile, both).remonttityyppi == LOCK  # highest confidence wins
        assert _wants(profile, facade) is None


def test_city_filter_is_case_insensitive_and_optional(app):
    with app.app_context():
        org = _org("cities")
        everywhere = _profile(org, name="all")
        helsinki = _profile(org, name="hki", cities=" helsinki ,VANTAA,, helsinki")
        assert helsinki.cities == ["Helsinki", "VANTAA"]

        espoo = _listing(1, kaupunki="Espoo")
        hki = _listing(2, kaupunki="Helsinki")
        vantaa = _listing(3, kaupunki="vantaa")

        assert _wants(everywhere, espoo) is not None
        assert _wants(helsinki, espoo) is None
        assert _wants(helsinki, hki) is not None
        assert _wants(helsinki, vantaa) is not None


def test_timing_filter_keeps_unknown_years(app):
    with app.app_context():
        org = _org("timing")
        profile = _profile(org, timing_within_years=2)  # NOW is 2026 -> up to 2028

        soon = _listing(1, renovations=[(LOCK, 90, 2027)])
        late = _listing(2, renovations=[(LOCK, 90, 2032)])
        unknown = _listing(3, renovations=[(LOCK, 90, None)])

        assert _wants(profile, soon) is not None
        assert _wants(profile, late) is None
        assert _wants(profile, unknown) is not None


def test_building_filters_keep_unknown_values(app):
    with app.app_context():
        org = _org("building")
        profile = _profile(org, build_year_min=1960, build_year_max=1985, building_types=["Kerrostalo"])

        ok = _listing(1, build_year=1972, building_type="Kerrostalo")
        too_new = _listing(2, build_year=2005, building_type="Kerrostalo")
        row_house = _listing(3, build_year=1972, building_type="Rivitalo")
        unknown = _listing(4)

        assert _wants(profile, ok) is not None
        assert _wants(profile, too_new) is None
        assert _wants(profile, row_house) is None
        assert _wants(profile, unknown) is not None


def test_filter_validation(app):
    with app.app_context():
        org = _org("validation")
        with pytest.raises(SearchProfileServiceError, match="alaraja"):
            _profile(org, build_year_min=1990, build_year_max=1980)
        with pytest.raises(SearchProfileServiceError, match="talotyypit"):
            _profile(org, building_types=["Linna"])
        with pytest.raises(SearchProfileServiceError, match="remonttityyppi"):
            _profile(org, remonttityypit=["Keksitty remontti"])
        with pytest.raises(SearchProfileServiceError, match="ajankohta"):
            _profile(org, timing_within_years="sata")

        # choosing every building type is the same as no restriction
        profile = _profile(
            org, building_types=["Kerrostalo", "Rivitalo", "Paritalo", "Omakotitalo", "Muu"]
        )
        assert profile.building_types == []

        # an old single-type update keeps the other filters untouched
        profile = _profile(org, name="keep", cities=["Espoo"], remonttityypit=[PIPE])
        update_profile(profile, name="keep", remonttityyppi="Julkisivu- ja parvekeurakointi", regions=["Uusimaa"], is_active=True)
        assert profile.cities == ["Espoo"]
        assert profile.wanted_types == ["Julkisivu- ja parvekeurakointi"]
        assert profile.needs_rematch is True


def test_rematch_and_preview_use_the_same_filter(app):
    with app.app_context():
        org = _org("rematch")
        _listing(1, kaupunki="Helsinki", renovations=[(PIPE, 80, None)])
        _listing(2, kaupunki="Espoo", renovations=[(PIPE, 80, None)])
        _listing(3, kaupunki="Helsinki", renovations=[(LOCK, 40, None)])  # below min confidence
        profile = _profile(org, remonttityypit=[PIPE], cities=["Helsinki"])
        db.session.commit()

        preview = svc.preview_profile(profile, now=NOW)
        assert preview["count"] == 1
        assert preview["sample"][0]["kaupunki"] == "Helsinki"

        assert svc.rematch_profile(profile, now=NOW) == 1
        match = ListingMatch.query.filter_by(profile_id=profile.id).one()
        assert match.listing.external_id == "1"


def test_ingest_and_analysis_fill_building_data_and_timing_year(app):
    with app.app_context():
        svc.ingest_page(
            maakunta="Uusimaa",
            mode="incremental",
            offset=0,
            page_in_run=1,
            listings=[
                {"external_id": "1", "description": "a", "build_year": "1974", "building_type": 1},
                {"external_id": "2", "description": "b", "build_year": None, "building_type": "luhtitalo"},
                {"external_id": "3", "description": "c"},
            ],
        )
        one, two, three = (Listing.query.filter_by(external_id=str(i)).one() for i in (1, 2, 3))
        assert (one.build_year, one.building_type) == (1974, "Kerrostalo")
        assert (two.build_year, two.building_type) == (None, "Kerrostalo")

        svc.save_analysis(
            three.id,
            {
                "status": "done",
                "rakennusvuosi": 1988,
                "talotyyppi": "Rivitalo",
                "renovations": [
                    {"remonttityyppi": LOCK, "confidence": 80, "timing": "PTS 2028–2030"},
                    {"remonttityyppi": PIPE, "confidence": 70, "timing": "suunnitteilla"},
                ],
            },
        )
        assert (three.build_year, three.building_type) == (1988, "Rivitalo")
        years = {r.remonttityyppi: r.timing_year for r in three.renovations}
        assert years == {LOCK: 2028, PIPE: None}

        # the card's own value is never overwritten by the AI
        svc.save_analysis(one.id, {"status": "done", "rakennusvuosi": 1999, "talotyyppi": "Rivitalo", "renovations": []})
        assert (one.build_year, one.building_type) == (1974, "Kerrostalo")


# --- leads: one per housing company, never merged by manager email -----------


def test_same_manager_email_gives_a_lead_per_housing_company(app):
    """One property manager serves many housing companies: their shared email
    must not collapse separate renovation leads into one."""
    with app.app_context():
        org = _org("manager")
        profile = _profile(org)
        for ext, company in ((1, "As Oy Ensimmäinen"), (2, "As Oy Toinen")):
            listing = _listing(
                ext, taloyhtio=company, isannoitsija="Isännöinti Oy", isannoitsija_email="info@isannointi.fi"
            )
            svc.match_listing(listing, now=NOW)
        db.session.commit()

        assert svc.materialize_pending_matches(now=NOW) == 2
        leads = Lead.query.filter_by(organization_id=org.id).order_by(Lead.source_ref).all()
        assert [l.source_ref for l in leads] == ["oikotie-1", "oikotie-2"]
        assert {l.email for l in leads} == {"info@isannointi.fi"}
        assert "Ensimmäinen" in leads[0].notes and "Toinen" in leads[1].notes


def test_second_apartment_of_same_housing_company_joins_the_existing_lead(app):
    with app.app_context():
        org = _org("company")
        profile = _profile(org, daily_lead_limit=1)
        first = _listing(1, taloyhtio="As Oy Sama", kaupunki="Espoo")
        second = _listing(2, taloyhtio="  as oy sama ", kaupunki="espoo")
        elsewhere = _listing(3, taloyhtio="As Oy Sama", kaupunki="Oulu", maakunta="Uusimaa")
        svc.match_listing(first, now=NOW)
        db.session.commit()
        assert svc.materialize_pending_matches(now=NOW) == 1

        svc.match_listing(second, now=NOW)
        svc.match_listing(elsewhere, now=NOW)
        db.session.commit()
        # the daily limit (1) is used up, but joining an existing lead costs nothing
        assert svc.materialize_pending_matches(now=NOW) == 0

        lead = Lead.query.filter_by(organization_id=org.id).one()
        merged = ListingMatch.query.filter_by(listing_id=second.id).one()
        assert (merged.status, merged.lead_id) == ("merged", lead.id)
        # same name in another city is a different housing company: it waits for quota
        assert ListingMatch.query.filter_by(listing_id=elsewhere.id).one().status == "pending"
        assert svc.profile_match_stats(profile)["merged"] == 1

        # contacts found later for the second apartment reach the shared lead,
        # which keeps its own property data
        second.contact_status = "processing"
        db.session.commit()
        result = svc.save_contacts(second.id, {"status": "done", "phone": "040 111 2222", "isannoitsija": "Isännöinti Oy"})
        assert result["leads_updated"] == 1
        lead = Lead.query.filter_by(organization_id=org.id).one()
        assert lead.phone == "040 111 2222"
        assert lead.source_ref == "oikotie-1"
        assert Lead.query.filter_by(organization_id=org.id).count() == 1


# --- retention + health -------------------------------------------------------


def test_removed_listings_are_purged_after_retention(app):
    with app.app_context():
        org = _org("retention")
        profile = _profile(org)
        old = _listing(1, is_active=False, removed_at=NOW - timedelta(days=120))
        recent = _listing(2, is_active=False, removed_at=NOW - timedelta(days=10))
        active = _listing(3)
        db.session.add(
            ListingMatch(organization_id=org.id, profile_id=profile.id, listing_id=old.id, status="lead_created")
        )
        db.session.commit()
        old_id = old.id

        assert svc.purge_removed_listings(now=NOW) == 1
        assert db.session.get(Listing, old_id) is None
        assert ListingMatch.query.filter_by(listing_id=old_id).count() == 0
        assert ListingRenovation.query.filter_by(listing_id=old_id).count() == 0
        assert {l.external_id for l in Listing.query.all()} == {"2", "3"}
        assert recent.is_active is False and active.is_active is True


def test_health_reports_stalled_crawl_and_errors_and_alerts_once(app):
    with app.app_context():
        superadmin = create_user("sa-health@test.com", "securepassword1", role="superadmin")
        db.session.commit()

        fresh = svc.catalogue_health(now=NOW)
        assert fresh["started"] is False and fresh["problems"] == []

        db.session.add(
            CrawlState(source="oikotie", maakunta="Uusimaa", last_incremental_at=NOW - timedelta(minutes=30))
        )
        _listing(1)
        db.session.commit()
        assert svc.catalogue_health(now=NOW)["problems"] == []

        state = CrawlState.query.filter_by(maakunta="Uusimaa").one()
        state.last_incremental_at = NOW - timedelta(hours=9)
        state.last_error = "HTTP 403"
        state.last_error_at = NOW - timedelta(hours=8)
        _listing(2, analysis_status="pending", first_seen_at=NOW - timedelta(hours=10), renovations=[])
        db.session.commit()

        problems = " ".join(svc.catalogue_health(now=NOW)["problems"])
        assert "Keruu (Crawler)" in problems
        assert "Uusimaa" in problems
        assert "Analyysi (Analyzer)" in problems

        with patch("app.email.services.EmailService.send_catalogue_alert") as send:
            send.return_value = {"success": True}
            assert svc.alert_catalogue_problems(now=NOW) == 1
            assert send.call_args.kwargs["to_email"] == superadmin.email
            # not repeated within the alert interval
            assert svc.alert_catalogue_problems(now=NOW + timedelta(hours=1)) == 0
            assert send.call_count == 1
        assert AuditLog.query.filter_by(action="catalogue_alert_sent").count() == 1


# --- onboarding rules ---------------------------------------------------------


def _customer(app, slug, **fields):
    actor = create_user(f"sa-{slug}@test.com", "securepassword1", role="superadmin")
    actor.totp_enabled = True
    db.session.commit()
    result = create_customer(
        actor=actor,
        organization_name=f"Asiakas {slug}",
        admin_email=f"admin-{slug}@asiakas.fi",
        admin_password="temporarypassword1",
        admin_name=None,
        search_profile_name="Profiili",
        regions=["Uusimaa"],
        remonttityyppi=LOCK,
        **fields,
    )
    result.admin_user.totp_enabled = True  # 2FA setup is covered elsewhere
    db.session.commit()
    return result


def _login(client, email, password):
    client.post("/auth/login", data={"email": email, "password": password})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True


def test_temporary_password_must_be_changed_before_using_the_app(app, client):
    with app.app_context():
        result = _customer(app, "pw")
        email = result.admin_user.email

    _login(client, email, "temporarypassword1")
    blocked = client.get("/leads/")
    assert blocked.status_code == 302
    assert "/auth/change-password" in blocked.headers["Location"]
    assert client.get("/auth/change-password").status_code == 200

    mismatch = client.post(
        "/auth/change-password",
        data={"password": "myownpassword12", "password_confirm": "different123456"},
    )
    assert mismatch.status_code == 200
    assert client.get("/leads/").status_code == 302

    done = client.post(
        "/auth/change-password",
        data={"password": "myownpassword12", "password_confirm": "myownpassword12"},
    )
    assert done.status_code == 302
    assert client.get("/leads/").status_code == 200

    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.must_change_password is False

    fresh = app.test_client()
    with app.app_context():
        fresh.post("/auth/login", data={"email": email, "password": "temporarypassword1"})
        assert fresh.get("/leads/").status_code in (302, 401)  # the old password no longer works


def test_optional_modules_are_enforced_and_editable_by_superadmin(app, client):
    with app.app_context():
        result = _customer(app, "features")
        org_id = result.organization.id
        email = result.admin_user.email
        result.admin_user.must_change_password = False
        db.session.commit()
        assert result.organization.enabled_features == ["sequences", "automations", "ai_enrichment"]

    # superadmin narrows the organization to automations only
    _login(client, "sa-features@test.com", "securepassword1")
    response = client.post(f"/admin/customers/{org_id}/features", data={"features": ["automations", "bogus"]})
    assert response.status_code == 302
    page = client.get("/admin/customers").get_data(as_text=True)
    assert "Asiakas features" in page and "Sähköpostisekvenssit" in page
    # superadmins are never restricted
    assert client.get(f"/sequences?organization_id={org_id}").status_code == 200
    client.get("/auth/logout")

    with app.app_context():
        assert db.session.get(Organization, org_id).enabled_features == ["automations"]
        from app.core.features import organization_has_feature

        assert organization_has_feature(org_id, "automations") is True
        assert organization_has_feature(org_id, "ai_enrichment") is False

        customer = app.test_client()
        _login(customer, email, "temporarypassword1")
        denied = customer.get("/sequences")
        assert denied.status_code == 302 and "/sequences" not in denied.headers["Location"]
        assert customer.get("/automations").status_code == 200


def test_superadmin_catalogue_page_and_profile_preview(app, client):
    with app.app_context():
        result = _customer(app, "pages")
        org_id = result.organization.id
        _listing(1, renovations=[(LOCK, 90, None)], first_seen_at=datetime.now(timezone.utc),
                 last_seen_at=datetime.now(timezone.utc), published_at=datetime.now(timezone.utc))
        db.session.add(CrawlState(source="oikotie", maakunta="Uusimaa", last_error="HTTP 403",
                                  last_incremental_at=datetime.now(timezone.utc)))
        db.session.commit()

    _login(client, "sa-pages@test.com", "securepassword1")
    page = client.get("/admin/catalogue")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Kohdekatalogin tila" in text and "HTTP 403" in text and "Keruuvirhe maakunnissa" in text

    form = client.get(f"/settings/search-profiles?organization_id={org_id}").get_data(as_text=True)
    assert 'name="remonttityypit"' in form and 'name="cities"' in form and "Koko Suomi" in form

    preview = client.post(
        f"/settings/search-profiles/preview?organization_id={org_id}",
        data={"remonttityypit": [LOCK], "region_mode": "all_finland"},
    )
    assert preview.status_code == 200
    assert preview.get_json()["count"] == 1

    invalid = client.post(
        f"/settings/search-profiles/preview?organization_id={org_id}",
        data={"remonttityypit": [LOCK], "region_mode": "specific"},
    )
    assert invalid.status_code == 400
    with app.app_context():
        from app.search.models import SearchProfile

        assert SearchProfile.query.filter_by(organization_id=org_id).count() == 1  # preview saved nothing
