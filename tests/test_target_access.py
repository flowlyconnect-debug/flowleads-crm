"""Kohteet without the old "Hae kohteita" reserve, and the per-customer target
access switch (Organization.targets_enabled, /admin/customers)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.billing.services import targets_locked
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.notifications.models import Notification
from app.search.models import SearchProfile
from app.users.models import Organization
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
PIPE = "Putkiremontti"
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


def _listing(ext: int, *, company: str | None = None) -> Listing:
    listing = Listing(
        source="import",
        external_id=str(ext),
        maakunta="Uusimaa",
        kaupunki="Espoo",
        osoite=f"Testitie {ext}",
        taloyhtio=company or f"As Oy Kohde {ext}",
        isannoitsija=f"Isännöinti {ext} Oy",
        analysis_status="done",
        first_seen_at=NOW,
        last_seen_at=NOW,
        published_at=NOW - timedelta(minutes=ext),
    )
    db.session.add(listing)
    db.session.flush()
    db.session.add(ListingRenovation(listing_id=listing.id, remonttityyppi=PIPE, confidence=90))
    db.session.flush()
    return listing


def _match(count: int, start: int = 1) -> list[Listing]:
    """Listings matched the way the app does it (match_listing)."""
    listings = []
    for ext in range(start, start + count):
        listing = _listing(ext)
        svc.match_listing(listing, now=NOW)
        listings.append(listing)
    db.session.commit()
    return listings


def _login(client, email: str):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def _superadmin():
    user = create_user("root-access@test.com", PASSWORD, role="superadmin", organization_id=None)
    user.totp_enabled = True
    db.session.commit()
    return user


# --- no reserve: every match is a target at once -------------------------------------


def test_every_match_is_visible_at_once(client, app):
    with app.app_context():
        org, user, _ = _org("all")
        _match(12)
        assert targets.count_targets(org.id) == 12
        assert all(m.released_at is not None for m in ListingMatch.query.filter_by(organization_id=org.id))
        email = user.email
    _login(client, email)
    page = client.get("/targets").get_data(as_text=True)
    assert "12 kohdetta saatavilla" in page and "As Oy Kohde 7" in page
    assert "Hae kohteita</button>" not in page and "targets-fetch-btn" not in page
    assert client.post("/targets/fetch").status_code == 404


def test_old_reserve_rows_are_visible_too(app):
    """Matches stored by the previous release with released_at NULL."""
    with app.app_context():
        org, _, _ = _org("oldreserve")
        _match(3)
        ListingMatch.query.filter_by(organization_id=org.id).update({"released_at": None})
        db.session.commit()
        assert targets.count_targets(org.id) == 3


def test_digest_tells_how_many_new_targets(app):
    with app.app_context():
        org, user, _ = _org("digest")
        _match(4)
        assert targets.send_target_digests(now=NOW + timedelta(hours=1)) == 1
        note = Notification.query.filter_by(user_id=user.id, type="targets_new").one()
        assert note.title == "4 uutta kohdetta" and "Hae kohteita" not in note.message


# --- the switch ---------------------------------------------------------------------


def test_superadmin_switches_target_access(client, app):
    with app.app_context():
        org, _, _ = _org("switch")
        root = _superadmin()
        org_id, email = org.id, root.email
        assert org.targets_enabled is True and not targets_locked(org_id)
    _login(client, email)
    page = client.get("/admin/customers").get_data(as_text=True)
    assert "Kohteet käytössä" in page
    response = client.post(f"/admin/customers/{org_id}/targets-access", data={"enabled": "0"}, follow_redirects=True)
    assert "Kohteet pois" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Organization, org_id).targets_enabled is False and targets_locked(org_id)
    client.post(f"/admin/customers/{org_id}/targets-access", data={"enabled": "1"})
    with app.app_context():
        assert not targets_locked(org_id)


def test_only_a_superadmin_can_switch(client, app):
    with app.app_context():
        org, user, _ = _org("noswitch")
        org_id, email = org.id, user.email
    _login(client, email)
    assert client.post(f"/admin/customers/{org_id}/targets-access", data={"enabled": "0"}).status_code in (302, 403)
    with app.app_context():
        assert db.session.get(Organization, org_id).targets_enabled is True


def test_locked_customer_sees_the_count_but_not_the_targets(client, app):
    with app.app_context():
        org, user, _ = _org("locked")
        listings = _match(5)
        first = ListingMatch.query.filter_by(organization_id=org.id, listing_id=listings[0].id).one()
        result = targets.transfer_targets(org.id, [first.id])
        lead_id = result["lead_ids"][0]
        pending = ListingMatch.query.filter_by(organization_id=org.id, status="pending").first()
        org.targets_enabled = False
        db.session.commit()
        email, match_id, org_id = user.email, pending.id, org.id
        lead_listing, target_listing = listings[0].id, pending.listing_id
        target_name, target_manager = pending.listing.taloyhtio, pending.listing.isannoitsija
        lead_manager = listings[0].isannoitsija

    _login(client, email)
    page = client.get("/targets").get_data(as_text=True)
    assert 'data-testid="targets-locked-count">4<' in page
    assert 'data-testid="targets-locked-count"' in page
    assert target_name not in page and "As Oy Kohde" not in page

    # the map, the hidden tab and every action lead back to the locked page
    assert client.get("/targets/map").status_code == 302
    assert "As Oy Kohde" not in client.get("/targets?tab=hidden").get_data(as_text=True)
    for path in ("/targets/transfer", "/targets/dismiss"):
        response = client.post(path, data={"single_id": str(match_id)}, follow_redirects=True)
        assert 'data-testid="targets-locked-count"' in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(ListingMatch, match_id).status == "pending"
        assert Lead.query.filter_by(organization_id=org_id).count() == 1

    # the dashboard and the sidebar still show the count, not the targets
    dashboard = client.get("/dashboard").get_data(as_text=True)
    assert "4 kohdetta saatavilla" in dashboard and target_name not in dashboard

    # what was moved to leads stays, with its housing-company card
    assert client.get(f"/leads/{lead_id}").status_code == 200
    assert client.get(f"/housing-companies/{lead_listing}").status_code == 200
    assert client.get(f"/housing-companies/{target_listing}").status_code == 404
    managers = client.get("/managers").get_data(as_text=True)
    assert lead_manager in managers and target_manager not in managers


def test_locked_customer_gets_no_target_notifications_or_new_signals(app):
    with app.app_context():
        org, user, _ = _org("quiet")
        listings = _match(2)
        first = ListingMatch.query.filter_by(organization_id=org.id, listing_id=listings[0].id).one()
        targets.transfer_targets(org.id, [first.id])
        targets.save_search(org.id, user.id, "Espoo", {"city": "Espoo"}, now=NOW - timedelta(hours=1))
        org.targets_enabled = False
        db.session.commit()

        # another apartment of the lead's housing company, and a new target
        again = _listing(10, company=listings[0].taloyhtio)
        svc.match_listing(again, now=NOW)
        _match(1, start=11)
        assert svc.merge_into_existing_leads(now=NOW) == 0
        assert targets.send_target_digests(now=NOW + timedelta(hours=1)) == 0
        assert targets.notify_saved_searches(now=NOW + timedelta(hours=1)) == 0
        assert Notification.query.filter_by(user_id=user.id).count() == 0
        # matching goes on, so the count the customer sees stays current
        assert targets.count_targets(org.id) == 2

        org.targets_enabled = True
        db.session.commit()
        assert targets.notify_saved_searches(now=NOW + timedelta(hours=2)) == 1


def test_superadmin_still_sees_a_locked_customers_targets(client, app):
    with app.app_context():
        org, _, _ = _org("rootview")
        _match(2)
        org.targets_enabled = False
        root = _superadmin()
        db.session.commit()
        org_id, email = org.id, root.email
    _login(client, email)
    page = client.get(f"/targets?organization_id={org_id}").get_data(as_text=True)
    assert "As Oy Kohde 1" in page


def test_locked_profile_page_names_only_leads(client, app):
    """Hakuprofiilit "Viimeisimmät osumat" must not list targets that are not leads."""
    with app.app_context():
        org, user, profile = _org("lockedprofile")
        listings = _match(4)
        first = ListingMatch.query.filter_by(organization_id=org.id, listing_id=listings[0].id).one()
        targets.transfer_targets(org.id, [first.id])
        db.session.get(Organization, org.id).targets_enabled = False
        db.session.commit()
        email, profile_id = user.email, profile.id
    _login(client, email)
    page = client.get(f"/settings/search-profiles?profile_id={profile_id}").get_data(as_text=True)
    assert "Testitie 1" in page
    for ext in (2, 3, 4):
        assert f"Testitie {ext}" not in page


def test_superadmin_is_told_the_customer_is_locked(client, app):
    with app.app_context():
        org, _, _ = _org("lockednote")
        _match(3)
        db.session.get(Organization, org.id).targets_enabled = False
        root = _superadmin()
        org_id, email = org.id, root.email
    _login(client, email)
    page = client.get(f"/targets?organization_id={org_id}").get_data(as_text=True)
    assert 'data-testid="targets-customer-locked"' in page and "As Oy Kohde 2" in page
    preview = client.get(f"/targets?organization_id={org_id}&customer_view=1").get_data(as_text=True)
    assert 'data-testid="targets-customer-preview"' in preview and "As Oy Kohde 2" not in preview
    assert 'data-testid="targets-locked-count">3<' in preview


def test_locked_page_sells_without_showing_targets(client, app, monkeypatch):
    """The locked page shows counts by renovation type and region and the plan,
    never a housing company, address or manager, and "Ota kohteet käyttöön"
    tells FlowLeads (once)."""
    sent = []
    monkeypatch.setattr("app.billing.services._notify_superadmins", lambda org, subject, body: sent.append(subject))
    with app.app_context():
        org, user, _ = _org("sell")
        _match(6)
        db.session.get(Organization, org.id).targets_enabled = False
        db.session.commit()
        email, org_id = user.email, org.id
        summary = targets.locked_target_summary(org_id)
        assert summary["total"] == 6 and summary["new_week"] == 6
        assert summary["by_type"][0] == {"name": PIPE, "count": 6, "share": 100}
        assert summary["by_region"][0]["name"] == "Uusimaa"
    _login(client, email)
    page = client.get("/targets").get_data(as_text=True)
    assert "149 € / kk" in page and PIPE in page and "Uusimaa" in page
    assert 'data-testid="targets-request-access"' in page
    for text in ("As Oy Kohde", "Testitie", "Isännöinti", "Espoo"):
        assert text not in page
    response = client.post("/targets/request-access", follow_redirects=True)
    assert 'data-testid="targets-access-requested"' in response.get_data(as_text=True)
    client.post("/targets/request-access")
    assert sent == ["Kohteet käyttöön: Org sell"]
    with app.app_context():
        assert targets_locked(org_id)  # a request does not open the targets


def test_request_access_is_a_no_op_when_targets_are_open(client, app, monkeypatch):
    sent = []
    monkeypatch.setattr("app.billing.services._notify_superadmins", lambda *a: sent.append(a))
    with app.app_context():
        _, user, _ = _org("open")
        email = user.email
    _login(client, email)
    assert client.post("/targets/request-access").status_code == 302
    assert sent == []
