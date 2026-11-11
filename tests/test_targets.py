"""Kohteet page: catalogue matches wait as targets until a user moves them to
leads. Nothing becomes a lead automatically (LISTING_AUTO_CREATE_LEADS off)."""

from datetime import datetime, timezone

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.search.models import SearchProfile
from app.users.services import create_organization, create_user

LOCK = "Lukitus, kulunvalvonta ja oviautomatiikka"
PIPE = "Putki- ja viemäriurakointi"
NOW = datetime.now(timezone.utc)
PASSWORD = "securepassword1"


def _org(slug, *, role="user"):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"{role}-{slug}@test.com", PASSWORD, role=role, organization_id=org.id)
    user.totp_enabled = True
    db.session.flush()
    return org, user


def _profile(org, *, rtype=LOCK, name="P", regions=("Uusimaa",)):
    profile = SearchProfile(
        organization_id=org.id,
        name=name,
        remonttityyppi=rtype,
        regions=list(regions),
        source="oikotie",
        is_active=True,
        min_confidence=60,
        needs_rematch=False,
    )
    db.session.add(profile)
    db.session.flush()
    return profile


def _listing(ext, *, renovations=((LOCK, 90),), **fields):
    data = dict(
        source="oikotie",
        external_id=str(ext),
        maakunta="Uusimaa",
        kaupunki="Espoo",
        osoite=f"Testitie {ext}",
        taloyhtio=f"As Oy Kohde {ext}",
        url=f"https://asunnot.oikotie.fi/myytavat-asunnot/espoo/{ext}",
        analysis_status="done",
        first_seen_at=NOW,
        last_seen_at=NOW,
        published_at=NOW,
    )
    data.update(fields)
    listing = Listing(**data)
    for rtype, confidence in renovations:
        listing.renovations.append(
            ListingRenovation(remonttityyppi=rtype, confidence=confidence, timing="2027", evidence="Lukitus uusitaan")
        )
    db.session.add(listing)
    db.session.flush()
    svc.match_listing(listing)
    db.session.commit()
    return listing


def _match_ids(org, status="pending"):
    return [m.id for m in ListingMatch.query.filter_by(organization_id=org.id, status=status).all()]


def _login(client, email):
    response = client.post("/auth/login", data={"email": email, "password": PASSWORD})
    assert response.status_code == 302
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True


# --- services ----------------------------------------------------------------


def test_analysed_listing_waits_as_target_instead_of_becoming_a_lead(app):
    with app.app_context():
        org, _ = _org("wait")
        _profile(org)
        listing = Listing(
            source="oikotie", external_id="1", maakunta="Uusimaa", first_seen_at=NOW, last_seen_at=NOW
        )
        db.session.add(listing)
        db.session.commit()
        result = svc.save_analysis(
            listing.id, {"status": "done", "renovations": [{"remonttityyppi": LOCK, "confidence": 80}]}
        )
        assert result["matches"] == 1 and result["leads_created"] == 0
        assert Lead.query.filter_by(organization_id=org.id).count() == 0
        assert targets.count_targets(org.id) == 1
        # contacts are still looked up for a matched listing
        assert db.session.get(Listing, listing.id).contact_status == "pending"


def test_transfer_creates_lead_and_removes_target(app):
    with app.app_context():
        org, _ = _org("transfer")
        other, _ = _org("transfer-other")
        _profile(org)
        _profile(other)
        _listing(1, isannoitsija="Isännöinti Oy", isannoitsija_puh="0401234567")
        _listing(2)
        assert targets.count_targets(org.id) == 2

        first = ListingMatch.query.filter_by(organization_id=org.id).order_by(ListingMatch.id).first()
        result = targets.transfer_targets(org.id, [first.id])
        assert result["created"] == 1 and result["failed"] == 0

        lead = Lead.query.filter_by(organization_id=org.id).one()
        assert lead.id == result["lead_ids"][0]
        assert lead.source_ref == "oikotie-1"
        assert lead.company == "Isännöinti Oy" and lead.phone == "0401234567"
        assert db.session.get(ListingMatch, first.id).status == "lead_created"
        assert targets.count_targets(org.id) == 1
        # the other organization's targets are untouched
        assert targets.count_targets(other.id) == 2
        assert Lead.query.filter_by(organization_id=other.id).count() == 0

        # doing it again does nothing
        again = targets.transfer_targets(org.id, [first.id])
        assert again == {"created": 0, "merged": 0, "failed": 0, "lead_ids": []}
        assert Lead.query.filter_by(organization_id=org.id).count() == 1


def test_transfer_ignores_other_organizations_matches(app):
    with app.app_context():
        org, _ = _org("mine")
        other, _ = _org("theirs")
        _profile(other)
        _listing(1)
        foreign = _match_ids(other)
        assert targets.transfer_targets(org.id, foreign)["created"] == 0
        assert targets.dismiss_targets(org.id, foreign) == 0
        assert Lead.query.count() == 0
        assert targets.count_targets(other.id) == 1


def test_listing_matched_by_two_profiles_is_one_target(app):
    with app.app_context():
        org, _ = _org("two-profiles")
        _profile(org, name="Lukot")
        _profile(org, rtype=PIPE, name="Putket")
        _listing(1, renovations=((LOCK, 90), (PIPE, 80)))
        assert ListingMatch.query.filter_by(organization_id=org.id).count() == 2
        assert targets.count_targets(org.id) == 1
        page = targets.list_targets(org.id)
        assert page.total == 1

        result = targets.transfer_targets(org.id, [page.items[0].id])
        assert result["created"] == 1
        assert Lead.query.filter_by(organization_id=org.id).count() == 1
        assert targets.count_targets(org.id) == 0
        statuses = {m.status for m in ListingMatch.query.filter_by(organization_id=org.id).all()}
        assert statuses == {"lead_created", "merged"}


def test_second_apartment_of_same_housing_company_joins_the_lead(app):
    with app.app_context():
        org, _ = _org("same-yhtio")
        _profile(org)
        _listing(1, taloyhtio="As Oy Sama")
        _listing(2, taloyhtio="As Oy Sama")
        ids = _match_ids(org)
        assert targets.transfer_targets(org.id, ids[:1])["created"] == 1

        # the scheduler joins the other apartment to the lead; no new lead
        assert svc.merge_into_existing_leads() == 1
        assert targets.count_targets(org.id) == 0
        assert Lead.query.filter_by(organization_id=org.id).count() == 1


def test_dismiss_and_restore(app):
    with app.app_context():
        org, _ = _org("dismiss")
        profile = _profile(org)
        _listing(1)
        ids = _match_ids(org)
        assert targets.dismiss_targets(org.id, ids) == 1
        assert targets.count_targets(org.id) == 0
        assert targets.count_targets(org.id, "dismissed") == 1

        # a rematch (filter change) does not bring a hidden target back
        profile.needs_rematch = True
        db.session.commit()
        svc.rematch_profiles()
        assert targets.count_targets(org.id) == 0

        assert targets.restore_targets(org.id, ids) == 1
        assert targets.count_targets(org.id) == 1


def test_removed_listing_is_not_a_target(app):
    with app.app_context():
        org, _ = _org("removed")
        _profile(org)
        listing = _listing(1)
        listing.is_active = False
        db.session.commit()
        assert targets.count_targets(org.id) == 0
        assert targets.transfer_targets(org.id, _match_ids(org))["failed"] == 1
        assert Lead.query.count() == 0


def test_search_and_profile_filter(app):
    with app.app_context():
        org, _ = _org("filter")
        locks = _profile(org, name="Lukot")
        pipes = _profile(org, rtype=PIPE, name="Putket")
        _listing(1, taloyhtio="As Oy Koivu", kaupunki="Espoo")
        _listing(2, taloyhtio="As Oy Mänty", kaupunki="Vantaa", renovations=((PIPE, 90),))
        assert targets.list_targets(org.id).total == 2
        assert [m.listing.taloyhtio for m in targets.list_targets(org.id, search="koivu").items] == ["As Oy Koivu"]
        assert targets.list_targets(org.id, search="vantaa").total == 1
        assert targets.list_targets(org.id, profile_id=pipes.id).total == 1
        assert targets.list_targets(org.id, profile_id=locks.id).items[0].listing.external_id == "1"


def test_scheduler_job_does_not_create_leads(app):
    with app.app_context():
        org, _ = _org("sched")
        profile = _profile(org)
        profile.needs_rematch = True
        _listing(1)
        db.session.commit()
        assert svc.auto_create_leads() is False
        svc.rematch_profiles()
        svc.merge_into_existing_leads()
        assert Lead.query.count() == 0
        assert targets.count_targets(org.id) == 1


# --- pages -------------------------------------------------------------------


def test_targets_page_lists_and_transfers(app, client):
    with app.app_context():
        org, user = _org("page")
        _profile(org)
        _listing(1, taloyhtio="As Oy Näkyvä", isannoitsija="Isännöinti Näkyvä Oy")
        _listing(2, taloyhtio="As Oy Toinen")
        org_id, email = org.id, user.email
        ids = _match_ids(org)

    assert client.get("/targets").status_code in (302, 401)
    _login(client, email)

    page = client.get("/targets")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "As Oy Näkyvä" in html and "As Oy Toinen" in html
    assert "2 kohdetta saatavilla" in html
    assert "Isännöinti Näkyvä Oy" in html and LOCK in html

    # one row's own button
    response = client.post("/targets/transfer", data={"single_id": ids[0], "match_ids": ids}, follow_redirects=True)
    assert response.status_code == 200
    assert "1 kohde siirretty liidiksi" in response.get_data(as_text=True)
    with app.app_context():
        assert Lead.query.filter_by(organization_id=org_id).count() == 1

    # hide the other one, then bring it back and move it with the bulk action
    response = client.post("/targets/dismiss", data={"match_ids": [ids[1]]}, follow_redirects=True)
    assert "1 kohde piilotettu" in response.get_data(as_text=True)
    assert "As Oy Toinen" in client.get("/targets?tab=hidden").get_data(as_text=True)
    assert "Ei uusia kohteita" in client.get("/targets").get_data(as_text=True)
    client.post("/targets/restore", data={"match_ids": [ids[1]], "tab": "hidden"})
    client.post("/targets/transfer", data={"match_ids": [ids[1]]})
    with app.app_context():
        assert Lead.query.filter_by(organization_id=org_id).count() == 2
        assert targets.count_targets(org_id) == 0

    empty = client.post("/targets/transfer", data={}, follow_redirects=True)
    assert "Valitse vähintään yksi kohde" in empty.get_data(as_text=True)


def test_user_cannot_touch_another_organizations_targets(app, client):
    with app.app_context():
        org, user = _org("iso-a")
        other, _ = _org("iso-b")
        _profile(other)
        _listing(1, taloyhtio="As Oy Salainen")
        email, other_id = user.email, other.id
        foreign = _match_ids(other)

    _login(client, email)
    assert "As Oy Salainen" not in client.get("/targets").get_data(as_text=True)
    client.post("/targets/transfer", data={"match_ids": foreign})
    client.post("/targets/dismiss", data={"match_ids": foreign})
    with app.app_context():
        assert Lead.query.count() == 0
        assert targets.count_targets(other_id) == 1


def test_dashboard_and_sidebar_show_available_targets(app, client):
    with app.app_context():
        org, user = _org("dash", role="admin")
        _profile(org)
        _listing(1)
        _listing(2)
        _listing(3)
        email = user.email

    _login(client, email)
    html = client.get("/dashboard").get_data(as_text=True)
    assert "kohdetta saatavilla" in html
    assert 'class="dash-targets__count">3<' in html
    assert 'class="sidebar-badge">3<' in html


def test_superadmin_customers_list_shows_targets_per_customer(app, client):
    with app.app_context():
        org, _ = _org("cust")
        _profile(org)
        _listing(1)
        _listing(2)
        admin = create_user("root-targets@test.com", PASSWORD, role="superadmin")
        admin.totp_enabled = True
        db.session.commit()
        email, org_id = admin.email, org.id

    _login(client, email)
    html = client.get("/admin/customers").get_data(as_text=True)
    assert "2 kohdetta saatavilla" in html
    assert client.get(f"/targets?organization_id={org_id}").status_code == 200
