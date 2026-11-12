"""One housing company across listings: grouping keys, one target per company,
the target score and the signal timeline on the housing-company card."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.listings import housing
from app.listings import insight_services as insights
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.search.models import SearchProfile
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
PIPE = "Putkiremontti"
ROOF = "Katto- ja vesikattoremontti"
NOW = datetime.now(timezone.utc)


def _org(slug, rtypes=(PIPE,)):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    user.totp_enabled = True
    profile = SearchProfile(
        organization_id=org.id,
        name="P",
        remonttityyppi=rtypes[0],
        remonttityypit=list(rtypes),
        regions=["Uusimaa"],
        source="oikotie",
        is_active=True,
        min_confidence=50,
        needs_rematch=False,
    )
    db.session.add(profile)
    db.session.commit()
    return org, user, profile


def _listing(ext, *, taloyhtio="As Oy Kallio", city="Espoo", ytunnus=None, renovations=((PIPE, 80, "2027"),), published=None, **fields):
    listing = Listing(
        source="import",
        external_id=str(ext),
        maakunta="Uusimaa",
        kaupunki=city,
        osoite=f"Kalliotie {ext}",
        taloyhtio=taloyhtio,
        ytunnus=ytunnus,
        analysis_status="done",
        first_seen_at=NOW,
        last_seen_at=NOW,
        published_at=published or NOW,
        **fields,
    )
    for rtype, confidence, timing in renovations:
        listing.renovations.append(
            ListingRenovation(
                remonttityyppi=rtype,
                confidence=confidence,
                timing=timing,
                timing_year=int(timing[:4]) if timing and timing[:4].isdigit() else None,
                evidence=f"{rtype} tulossa",
            )
        )
    db.session.add(listing)
    db.session.flush()
    housing.reconcile_company_key(listing)
    svc.match_listing(listing)
    db.session.commit()
    return listing


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


# --- keys ------------------------------------------------------------------------


def test_company_names_normalise_across_spellings():
    same = {
        housing.normalize_company_name(name)
        for name in ("As Oy Kallio", "As. Oy  KALLIO", "Asunto Oy Kallio", "Asunto-osakeyhtiö Kallio", "Bostads Ab Kallio")
    }
    assert same == {"as oy kallio"}
    assert housing.normalize_company_name("Kiinteistö Oy Kallio") == "koy kallio"
    assert housing.normalize_ytunnus("FI 1234567-8") == "1234567-8"
    assert housing.normalize_ytunnus("12345678") == "1234567-8"
    assert housing.normalize_ytunnus("123") is None
    # the city is part of the key: a same-named company elsewhere is another company
    assert housing.name_key("As Oy Kallio", "Espoo") != housing.name_key("As Oy Kallio", "Turku")


def test_keys_follow_name_city_and_ytunnus(app):
    with app.app_context():
        named = Listing(source="import", external_id="a", maakunta="Uusimaa", kaupunki="Espoo", taloyhtio="As Oy Kallio", ytunnus="1111111-1")
        nameless = Listing(source="import", external_id="b", maakunta="Uusimaa", kaupunki="Espoo", ytunnus="1111111-1")
        alone = Listing(source="import", external_id="c", maakunta="Uusimaa", osoite="Tie 1")
        db.session.add_all([named, nameless, alone])
        db.session.commit()
        assert named.company_key == "n:as oy kallio|espoo"
        assert nameless.company_key == "y:1111111-1"
        assert alone.company_key is None and housing.company_group(alone) == f"l:{alone.id}"
        # the Y-tunnus joins the nameless listing to the named company
        assert housing.reconcile_company_key(nameless) is True
        db.session.commit()
        assert nameless.company_key == named.company_key
        # renaming a listing moves it to its new company
        named.taloyhtio = "As Oy Uusi Kallio"
        db.session.commit()
        assert named.company_key == "n:as oy uusi kallio|espoo"


def test_shared_manager_ytunnus_never_merges_different_companies(app):
    """Import files have carried the property manager's Y-tunnus in the
    housing-company column: that must not turn many companies into one."""
    with app.app_context():
        org, _, _ = _org("sharedy")
        _listing(1, taloyhtio="As Oy Eka", ytunnus="0194158-2")
        _listing(2, taloyhtio="As Oy Toka", ytunnus="0194158-2")
        orphan = _listing(3, taloyhtio=None, ytunnus="0194158-2")
        assert targets.count_targets(org.id) == 3
        assert orphan.company_key == "y:0194158-2"  # ambiguous: stays on its own


def test_backfill_gives_old_listings_keys(app):
    with app.app_context():
        listing = Listing(source="import", external_id="old", maakunta="Uusimaa", kaupunki="Espoo", taloyhtio="As Oy Vanha")
        db.session.add(listing)
        db.session.commit()
        Listing.query.filter_by(id=listing.id).update({"company_key": None, "company_name_key": None})
        db.session.commit()
        assert housing.backfill_company_keys() == 1
        db.session.refresh(listing)
        assert listing.company_key == "n:as oy vanha|espoo"
        assert housing.backfill_company_keys() == 0  # idempotent


# --- one target per housing company ------------------------------------------------


def test_listings_of_one_company_are_one_target_with_all_signals(app):
    with app.app_context():
        org, _, _ = _org("onetarget", rtypes=(PIPE,))
        _listing(1, renovations=((PIPE, 70, "2029"),))
        _listing(2, taloyhtio="Asunto Oy Kallio", renovations=((PIPE, 90, "2027"),), isannoitsija="Isännöinti Oy", isannoitsija_puh="040 1")
        _listing(3, taloyhtio="As Oy Muu")
        assert targets.count_targets(org.id) == 2
        page = targets.list_targets(org.id)
        rows = targets.target_rows(org.id, page.items, targets.organization_wanted_types(org.id))
        kallio = next(r for r in rows if r["signal_count"] == 2)
        # the most confident mention and the contact come from the other listing
        assert kallio["renovation"].confidence == 90
        assert kallio["contact"].isannoitsija_puh == "040 1"

        # the contact filter is true for the company when any listing has one
        filtered = targets.list_targets(org.id, filters={"contact": "1"})
        assert filtered.total == 1


def test_hide_and_restore_whole_company(app):
    with app.app_context():
        org, _, _ = _org("hidecompany")
        first = _listing(1)
        _listing(2, taloyhtio="As. Oy Kallio")
        match = ListingMatch.query.filter_by(organization_id=org.id, listing_id=first.id).one()
        assert targets.dismiss_targets(org.id, [match.id]) == 1
        statuses = {m.status for m in ListingMatch.query.filter_by(organization_id=org.id)}
        assert statuses == {"dismissed"}
        assert all(m.dismissed_at is not None for m in ListingMatch.query.filter_by(organization_id=org.id))
        assert targets.count_targets(org.id) == 0
        # a new listing of the hidden company stays hidden
        _listing(3)
        assert targets.count_targets(org.id) == 0
        hidden = targets.list_targets(org.id, status="dismissed").items[0]
        assert targets.restore_targets(org.id, [hidden.id]) == 1
        assert targets.count_targets(org.id) == 1


def test_transfer_moves_every_listing_of_the_company_into_one_lead(app):
    with app.app_context():
        org, _, _ = _org("transfercompany")
        _listing(1)
        second = _listing(2)
        target = targets.list_targets(org.id).items[0]
        result = targets.transfer_targets(org.id, [target.id])
        assert result["created"] == 1
        lead = db.session.get(Lead, result["lead_ids"][0])
        assert {m.lead_id for m in ListingMatch.query.filter_by(organization_id=org.id)} == {lead.id}
        merged = ListingMatch.query.filter_by(listing_id=second.id).one()
        assert merged.status in ("merged", "lead_created")
        notes = [a.content for a in lead.activities if a.type == "updated"]
        assert any("Samasta taloyhtiöstä" in (n or "") for n in notes)


def test_fetch_releases_the_whole_company_and_new_signals_follow(app):
    with app.app_context():
        app.config["TARGET_FETCH_ENABLED"] = True
        try:
            org, _, _ = _org("fetchcompany")
            _listing(1)
            _listing(2, taloyhtio="Asunto Oy Kallio")
            _listing(3, taloyhtio="As Oy Toinen")
            assert targets.count_targets(org.id) == 0
            assert targets.count_reserve(org.id) == 2  # two companies
            import random

            result = targets.fetch_targets(org.id, now=NOW - timedelta(minutes=5), rng=random.Random(1))
            assert result["count"] == 2
            assert all(m.released_at is not None for m in ListingMatch.query.filter_by(organization_id=org.id))
            assert targets.count_targets(org.id) == 2
            # another listing of a visible company is visible at once, as part of it
            later = _listing(4)
            assert ListingMatch.query.filter_by(listing_id=later.id).one().released_at is not None
            assert targets.count_targets(org.id) == 2
            assert targets.count_reserve(org.id) == 0
        finally:
            app.config["TARGET_FETCH_ENABLED"] = False


# --- score ---------------------------------------------------------------------------


def test_score_parts_and_bounds(app):
    with app.app_context():
        org, _, _ = _org("score")
        strong = _listing(
            1,
            renovations=((PIPE, 100, str(NOW.year)),),
            isannoitsija="Isännöinti Oy",
            isannoitsija_puh="040 1",
            isannoitsija_email="a@b.fi",
        )
        _listing(2, taloyhtio="As Oy Kallio", renovations=((PIPE, 60, ""),))
        score = housing.score_target([strong, Listing.query.filter_by(external_id="2").one()], {PIPE}, now=NOW)
        parts = {p["key"]: p["points"] for p in score["parts"]}
        assert parts == {"confidence": 40, "timing": 25, "contact": 15, "signals": 6, "fresh": 10}
        assert score["total"] == 96

        weak = Listing(source="import", external_id="w", maakunta="Uusimaa", published_at=NOW - timedelta(days=200))
        weak.renovations.append(ListingRenovation(remonttityyppi=PIPE, confidence=50, timing=""))
        result = housing.score_target([weak], {PIPE}, now=NOW)
        assert 0 <= result["total"] <= 100
        assert {p["key"]: p["points"] for p in result["parts"]}["contact"] == 0
        # a renovation the organization does not do earns nothing
        assert housing.score_target([weak], {ROOF}, now=NOW)["parts"][0]["points"] == 0


def test_targets_sort_best_first_by_default(app):
    with app.app_context():
        org, _, _ = _org("bestfirst")
        _listing(1, taloyhtio="As Oy Heikko", renovations=((PIPE, 55, ""),), published=NOW - timedelta(days=1))
        _listing(2, taloyhtio="As Oy Vahva", renovations=((PIPE, 95, str(NOW.year)),), isannoitsija_puh="040", published=NOW - timedelta(days=3))
        housing.refresh_scores(org.id)
        names = [m.listing.taloyhtio for m in targets.list_targets(org.id).items]
        assert names == ["As Oy Vahva", "As Oy Heikko"]
        newest = [m.listing.taloyhtio for m in targets.list_targets(org.id, filters={"sort": "newest"}).items]
        assert newest == ["As Oy Heikko", "As Oy Vahva"]


def test_refresh_scores_counts_the_rest_of_the_company(app):
    with app.app_context():
        org, _, _ = _org("refresh")
        first = _listing(1, renovations=((PIPE, 80, "2030"),))
        match = ListingMatch.query.filter_by(listing_id=first.id).one()
        alone = match.score
        _listing(2, renovations=((PIPE, 80, "2030"),), isannoitsija_email="x@y.fi")
        assert housing.refresh_scores(org.id) >= 1
        db.session.refresh(match)
        assert match.score > alone  # second signal and a contact
        assert housing.refresh_scores(org.id) == 0


# --- housing-company card ---------------------------------------------------------------


def test_card_has_score_timeline_and_age_hints(app):
    with app.app_context():
        org, _, _ = _org("card")
        first = _listing(1, build_year=1968, renovations=((PIPE, 85, "2027"),))
        _listing(2, taloyhtio="As. Oy Kallio", renovations=((PIPE, 70, "2028"),))
        target = targets.list_targets(org.id).items[0]
        targets.transfer_targets(org.id, [target.id])
        card = insights.get_company_card(org.id, first.id)
        assert len(card["listing_list"]) == 2
        assert 0 < card["score"] <= 100 and len(card["score_parts"]) == 5
        kinds = [event["kind"] for event in card["timeline"]]
        assert kinds.count("listing") == 2 and "renovation" in kinds and "lead" in kinds
        assert all(card["timeline"][i]["at"] >= card["timeline"][i + 1]["at"] for i in range(len(kinds) - 1))
        # pipes are mentioned, so no age hint for pipes (the profile only wants pipes)
        assert card["age_hints"] == []

        hints = housing.age_based_hints(1968, set(), set(), now=NOW)
        assert {h["type"] for h in hints} >= {PIPE, ROOF}
        assert housing.age_based_hints(2015, set(), set(), now=NOW) == []


def test_pages_show_score_and_signals(client, app):
    with app.app_context():
        org, user, _ = _org("pages")
        first = _listing(1)
        _listing(2, taloyhtio="Asunto Oy Kallio")
        email = user.email
        listing_id = first.id
    _login(client, email)
    page = client.get("/targets").get_data(as_text=True)
    assert "target-score" in page and "/100" in page
    assert "2 ilmoitusta samasta taloyhtiöstä" in page
    assert "Parhaat ensin" in page
    card = client.get(f"/housing-companies/{listing_id}").get_data(as_text=True)
    assert "Kohteen pisteet" in card and "Signaalit aikajanalla" in card and "Ilmoitus löytyi" in card
