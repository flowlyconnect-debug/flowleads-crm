"""Isännöitsijät, the housing-company card and saved searches on Kohteet."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.listings import insight_services as insights
from app.listings import target_services as targets
from app.listings.models import Listing, ListingMatch, ListingRenovation, SavedTargetSearch
from app.notifications.models import Notification
from app.search.models import SearchProfile
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
PIPE = "Putkiremontti"
ROOF = "Katto- ja vesikattoremontti"
NOW = datetime.now(timezone.utc)


def _org(slug: str, role: str = "admin"):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"{role}-{slug}@test.com", PASSWORD, role=role, organization_id=org.id)
    user.totp_enabled = True
    profile = SearchProfile(
        organization_id=org.id,
        name="P",
        remonttityyppi=PIPE,
        regions=["Uusimaa"],
        source="oikotie",
        is_active=True,
        min_confidence=60,
        needs_rematch=False,
    )
    db.session.add(profile)
    db.session.commit()
    return org, user, profile


def _target(org, profile, ext, *, company=None, city="Espoo", manager="Isännöinti Kallio Oy", rtype=PIPE, timing="2027", status="pending", created=None, released=True):
    listing = Listing(
        source="import",
        external_id=str(ext),
        maakunta="Uusimaa",
        kaupunki=city,
        osoite=f"Testitie {ext}",
        taloyhtio=company or f"As Oy Kohde {ext}",
        analysis_status="done",
        first_seen_at=NOW,
        last_seen_at=NOW,
        published_at=NOW - timedelta(minutes=int(ext)),
        build_year=1970,
        building_type="kerrostalo",
        isannoitsija=manager,
        isannoitsija_puh="040 111 2222" if manager else None,
        isannoitsija_email="toimisto@kallio.fi" if manager else None,
    )
    db.session.add(listing)
    db.session.flush()
    db.session.add(ListingRenovation(listing_id=listing.id, remonttityyppi=rtype, confidence=90, timing=timing, evidence="Linjasaneeraus tulossa"))
    match = ListingMatch(
        organization_id=org.id,
        profile_id=profile.id,
        listing_id=listing.id,
        status=status,
        confidence=90,
        created_at=created or NOW,
        released_at=(created or NOW) if released else None,
    )
    db.session.add(match)
    db.session.commit()
    return match


def _login(client, email: str):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


# --- property managers ---------------------------------------------------------------


def test_managers_group_companies_targets_and_leads(app):
    with app.app_context():
        org, user, profile = _org("mgr")
        first = _target(org, profile, 1)
        _target(org, profile, 2)
        _target(org, profile, 3, manager="Toinen Isännöinti Oy")
        _target(org, profile, 4, manager=None)
        result = targets.transfer_targets(org.id, [first.id])
        lead = db.session.get(Lead, result["lead_ids"][0])
        lead.last_contacted_at = NOW
        db.session.commit()

        managers = insights.list_managers(org.id)
        assert [m["name"] for m in managers] == ["Isännöinti Kallio Oy", "Toinen Isännöinti Oy"]
        kallio = managers[0]
        assert (kallio["company_count"], kallio["available_count"], kallio["open_lead_count"]) == (2, 1, 1)
        assert kallio["phone"] == "040 111 2222" and kallio["last_contacted_at"] is not None
        # case and spacing in the name do not split a manager in two
        assert insights.get_manager(org.id, "  isännöinti   KALLIO oy ")["key"] == kallio["key"]
        assert [m["name"] for m in insights.list_managers(org.id, "toinen")] == ["Toinen Isännöinti Oy"]


def test_manager_pages(client, app):
    with app.app_context():
        org, user, profile = _org("mgrpage")
        _target(org, profile, 1)
        other, _, other_profile = _org("mgrpage2")
        _target(other, other_profile, 9, company="As Oy Vieras", manager="Vieras Isännöinti Oy")
        email = user.email
    _login(client, email)
    page = client.get("/managers").get_data(as_text=True)
    assert "Isännöinti Kallio Oy" in page and "Vieras Isännöinti" not in page
    detail = client.get("/managers/view?name=Isännöinti Kallio Oy").get_data(as_text=True)
    assert "As Oy Kohde 1" in detail and "Putkiremontti" in detail and "Saatavilla" in detail
    # another organization's manager is not found
    assert client.get("/managers/view?name=Vieras Isännöinti Oy").status_code == 404


# --- housing company card -----------------------------------------------------------------


def test_company_card_collects_listings_renovations_and_lead(client, app):
    with app.app_context():
        org, user, profile = _org("card")
        first = _target(org, profile, 1, company="As Oy Mäntyrinne", city="Helsinki")
        second = _target(org, profile, 2, company="as oy  mäntyrinne", city="Helsinki", rtype=ROOF, timing="2029")
        _target(org, profile, 3, company="As Oy Mäntyrinne", city="Tampere")  # same name, other city
        targets.transfer_targets(org.id, [first.id])
        db.session.commit()
        card = insights.get_company_card(org.id, second.listing_id)
        assert len(card["listing_list"]) == 2  # the Tampere company is a different one
        assert [r["type"] for r in card["renovations"]] == [PIPE, ROOF]  # soonest first
        assert card["state"] == "lead" and len(card["leads"]) == 1
        assert card["build_year"] == 1970 and card["manager_contacts"][0]["name"] == "Isännöinti Kallio Oy"
        email, listing_id, lead_id = user.email, second.listing_id, card["leads"][0].id
    _login(client, email)
    page = client.get(f"/housing-companies/{listing_id}").get_data(as_text=True)
    assert "As Oy Mäntyrinne" in page and "Tulevat remontit" in page and "2029" in page
    assert f"/leads/{lead_id}" in page
    lead_page = client.get(f"/leads/{lead_id}").get_data(as_text=True)
    assert "Taloyhtiön tiedot" in lead_page and "Isännöitsijän kohteet" in lead_page


def test_card_and_managers_do_not_reveal_the_reserve_or_other_customers(client, app):
    app.config["TARGET_FETCH_ENABLED"] = True
    try:
        with app.app_context():
            org, user, profile = _org("leak")
            reserve = _target(org, profile, 1, company="As Oy Varasto", manager="Varasto Isännöinti Oy", released=False)
            other, _, other_profile = _org("leak2")
            foreign = _target(other, other_profile, 2, company="As Oy Vieras")
            email, reserve_listing, foreign_listing = user.email, reserve.listing_id, foreign.listing_id
        _login(client, email)
        assert client.get(f"/housing-companies/{reserve_listing}").status_code == 404
        assert client.get(f"/housing-companies/{foreign_listing}").status_code == 404
        assert "Varasto Isännöinti" not in client.get("/managers").get_data(as_text=True)
    finally:
        app.config["TARGET_FETCH_ENABLED"] = False


# --- saved searches ------------------------------------------------------------------------


def test_saved_search_is_applied_with_one_click(client, app):
    with app.app_context():
        org, user, profile = _org("saved")
        _target(org, profile, 1, city="Espoo")
        _target(org, profile, 2, city="Helsinki")
        email, org_id, user_id = user.email, org.id, user.id
    _login(client, email)
    # nothing to save without a filter
    response = client.post("/targets/searches", data={"name": "Tyhjä"}, follow_redirects=True)
    assert "vähintään yksi rajaus" in response.get_data(as_text=True)

    response = client.post(
        "/targets/searches", data={"name": "Helsingin putket", "city": "Helsinki", "renovation": PIPE}, follow_redirects=True
    )
    page = response.get_data(as_text=True)
    assert "Haku tallennettu: Helsingin putket" in page
    with app.app_context():
        saved = SavedTargetSearch.query.filter_by(organization_id=org_id, user_id=user_id).one()
        assert saved.params == {"city": "Helsinki", "renovation": PIPE} and saved.notify
        saved_id = saved.id
    page = client.get("/targets").get_data(as_text=True)
    assert "Helsingin putket" in page and "city=Helsinki" in page

    client.post(f"/targets/searches/{saved_id}/notify")
    with app.app_context():
        assert db.session.get(SavedTargetSearch, saved_id).notify is False
    assert client.post(f"/targets/searches/{saved_id}/delete").status_code == 302
    with app.app_context():
        assert SavedTargetSearch.query.count() == 0


def test_saved_search_belongs_to_its_user(client, app):
    with app.app_context():
        org, user, _ = _org("savedown")
        colleague = create_user("colleague-savedown@test.com", PASSWORD, role="user", organization_id=org.id)
        saved = targets.save_search(org.id, colleague.id, "Kollegan haku", {"city": "Espoo"})
        db.session.commit()
        email, saved_id = user.email, saved.id
    _login(client, email)
    assert client.post(f"/targets/searches/{saved_id}/delete").status_code == 404
    assert "Kollegan haku" not in client.get("/targets").get_data(as_text=True)


def test_new_matching_target_notifies_the_owner_once(app):
    with app.app_context():
        org, user, profile = _org("notify")
        _target(org, profile, 1, city="Helsinki")  # existed before the search was saved
        saved = targets.save_search(org.id, user.id, "Helsinki", {"city": "Helsinki"}, now=NOW + timedelta(minutes=1))
        db.session.commit()
        assert targets.notify_saved_searches(now=NOW + timedelta(minutes=2)) == 0

        _target(org, profile, 2, city="Espoo", created=NOW + timedelta(minutes=5))  # does not match
        assert targets.notify_saved_searches(now=NOW + timedelta(minutes=6)) == 0

        _target(org, profile, 3, city="Helsinki", created=NOW + timedelta(minutes=10))
        assert targets.notify_saved_searches(now=NOW + timedelta(minutes=11)) == 1
        note = Notification.query.filter_by(user_id=user.id).one()
        assert note.title == "Uusia kohteita: Helsinki" and "1 uusi kohde" in note.message
        assert note.link == "/targets?city=Helsinki"
        # not repeated for the same target
        assert targets.notify_saved_searches(now=NOW + timedelta(minutes=20)) == 0

        saved.notify = False
        _target(org, profile, 4, city="Helsinki", created=NOW + timedelta(minutes=30))
        db.session.commit()
        assert targets.notify_saved_searches(now=NOW + timedelta(minutes=31)) == 0


def test_saved_search_alert_with_the_reserve_points_to_the_button(app):
    app.config["TARGET_FETCH_ENABLED"] = True
    try:
        with app.app_context():
            org, user, profile = _org("notifyreserve")
            targets.save_search(org.id, user.id, "Helsinki", {"city": "Helsinki"}, now=NOW)
            _target(org, profile, 1, city="Helsinki", created=NOW + timedelta(minutes=5), released=False)
            assert targets.notify_saved_searches(now=NOW + timedelta(minutes=6)) == 1
            note = Notification.query.filter_by(user_id=user.id).one()
            assert "Hae kohteita" in note.message and "1 uusi" not in note.message
    finally:
        app.config["TARGET_FETCH_ENABLED"] = False


def test_navigation_follows_the_workflow(client, app):
    with app.app_context():
        _, user, _ = _org("navorder")
        email = user.email
    _login(client, email)
    page = client.get("/dashboard").get_data(as_text=True)
    order = [page.index(f'sidebar-item__label">{label}<') for label in ("Kojelauta", "Kohteet", "Liidit", "Myyntiputki", "Isännöitsijät", "Yritykset")]
    assert order == sorted(order)
