"""One taloyhtiö across every source (app/listings/housing_registry.py):
listings, PRH companies and Ryhti buildings resolve to the same company by
Y-tunnus, name and municipality, or street address."""

from __future__ import annotations

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.listings.housing_registry import Candidate, resolve, resolve_listings, resolve_registry
from app.listings.models import HousingCompany, Listing
from app.registry.models import RegistryBuilding, RegistryHousingCompany
from app.users.services import create_user


def _listing(ext, **fields):
    listing = Listing(source="import", external_id=ext, maakunta="Pirkanmaa", analysis_status="done", **fields)
    db.session.add(listing)
    db.session.commit()
    return listing


def _building(key, address, keys, town="Tampere", year=1972, company=None):
    building = RegistryBuilding(
        building_key=key, building_type="Kerrostalo", municipality=town, address=address, address_keys=keys,
        completion_year=year, apartments=24, addresses_fetched_at=db.func.now(), housing_company=company,
    )
    db.session.add(building)
    return building


def test_listing_prh_and_ryhti_are_one_company(app):
    with app.app_context():
        prh = RegistryHousingCompany(business_id="1234567-8", name="As. Oy Koivukuja 5", municipality="Tampere")
        db.session.add(prh)
        _building("b1", "Koivukuja 5", ["koivukuja5"], company=prh)
        db.session.commit()
        resolve_registry()
        _listing("l1", taloyhtio="Asunto Oy Koivukuja 5", kaupunki="Tampere", osoite="Koivukuja 5 B 12", build_year=1980)
        resolve_listings()

        assert HousingCompany.query.count() == 1
        company = HousingCompany.query.one()
        assert company.business_id == "1234567-8" and set(company.sources) == {"prh", "ryhti", "import"}
        assert company.build_year == 1972  # the building register wins over the listing's 1980
        assert Listing.query.one().housing_id == company.id == RegistryBuilding.query.one().housing_id


def test_a_nameless_building_is_named_by_a_listing_at_its_address(app):
    with app.app_context():
        _building("b2", "Hämeenpuisto 21", ["hameenpuisto21"])
        db.session.commit()
        resolve_registry()
        assert HousingCompany.query.one().name is None
        _listing("l2", taloyhtio="As Oy Hämeenpuisto 21", kaupunki="Tampere", osoite="Hämeenpuisto 21")
        resolve_listings()
        company = HousingCompany.query.one()
        assert company.name == "As Oy Hämeenpuisto 21" and set(company.sources) == {"ryhti", "import"}


def test_differently_named_companies_at_one_address_stay_apart(app):
    with app.app_context():
        resolve(Candidate(source="import", name="As Oy Kulma", municipality="Tampere", address="Kauppakatu 3"))
        other = resolve(Candidate(source="import", name="As Oy Kauppahovi", municipality="Tampere", address="Kauppakatu 3"))
        db.session.commit()
        assert HousingCompany.query.count() == 2 and other.name == "As Oy Kauppahovi"


def test_same_name_in_another_town_or_with_another_business_id_is_another_company(app):
    with app.app_context():
        resolve(Candidate(source="prh", name="As Oy Rinne", business_id="1111111-1", municipality="Tampere"))
        resolve(Candidate(source="prh", name="As Oy Rinne", business_id="2222222-2", municipality="Tampere"))
        resolve(Candidate(source="import", name="As Oy Rinne", municipality="Nokia"))
        db.session.commit()
        assert HousingCompany.query.count() == 3


def test_a_row_with_both_keys_merges_two_companies(app):
    with app.app_context():
        named = resolve(Candidate(source="import", name="As Oy Lehto", municipality="Tampere"))
        # PRH knows it by its manager's town; the listing by the building's
        by_bid = resolve(Candidate(source="prh", name="Asunto Oy Lehto", business_id="3333333-3", municipality="Pirkkala"))
        listing = _listing("l3", taloyhtio="As Oy Lehto", kaupunki="Tampere", housing_id=named.id)
        db.session.commit()
        assert HousingCompany.query.count() == 2
        resolve(Candidate(source="import", name="As Oy Lehto", business_id="3333333-3", municipality="Tampere"))
        db.session.commit()
        assert HousingCompany.query.count() == 1
        assert db.session.get(Listing, listing.id).housing_id == by_bid.id


def test_a_renamed_listing_is_resolved_again(app):
    with app.app_context():
        listing = _listing("l4", taloyhtio="As Oy Vanha", kaupunki="Tampere")
        resolve_listings()
        first = listing.housing_id
        listing.taloyhtio = "As Oy Uusi"
        db.session.commit()
        assert listing.housing_id is None
        resolve_listings()
        assert listing.housing_id not in (None, first)


def test_a_register_estimate_takes_its_buildings_company(app):
    with app.app_context():
        _building("b5", "Rinnekatu 31", ["rinnekatu31"])
        db.session.commit()
        resolve_registry()
        building = RegistryBuilding.query.one()
        estimate = Listing(source="ryhti", external_id="b5", maakunta="Pirkanmaa", analysis_status="done", is_estimate=True,
                           kaupunki="Tampere", osoite="Rinnekatu 31")
        db.session.add(estimate)
        db.session.commit()
        resolve_listings()
        assert estimate.housing_id == building.housing_id


def test_housing_page_is_superadmin_only(app, client):
    with app.app_context():
        resolve(Candidate(source="prh", name="As Oy Haettava", business_id="4444444-4", municipality="Tampere"))
        root = create_user("root-housing@test.com", "securepassword1", role="superadmin", organization_id=None)
        root.totp_enabled = True
        db.session.commit()
    client.post("/auth/login", data={"email": "root-housing@test.com", "password": "securepassword1"})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    html = client.get("/admin/housing?q=haettava").get_data(as_text=True)
    assert "As Oy Haettava" in html and "4444444-4" in html
