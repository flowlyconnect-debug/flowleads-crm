"""Public registers (app/registry/): Ryhti buildings, permits and addresses and
PRH housing companies are synced, linked and turned into signals. Internal
only: nothing reaches a customer (no listings, no matches, superadmin page)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.listings.models import Listing, ListingMatch
from app.registry.clients import PrhClient, RegistryFetchError, RyhtiClient
from app.registry.models import (
    RegistryBuilding,
    RegistryHousingCompany,
    RegistryPermit,
    RegistrySignal,
    RegistrySyncState,
)
from app.registry.services import run_registry_sync
from app.registry.signals import compute_signals
from app.registry.sync import address_key, link_companies, name_address_keys, sync_buildings
from app.users.services import create_organization, create_user

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
PASSWORD = "securepassword1"


def _building(key, perm_id, kunta, purpose, year, *, apartments=12, facade="Tiili", method="Paikalla tehty", energy="Kauko- tai aluelämpö", stamp="2026-11-11T10:00:00Z"):
    return {
        "properties": {
            "rakennusavain": key,
            "pysyva_rakennustunnus": perm_id,
            "sijaintikiinteisto": "83700000000001",
            "valmistumispaivamaara": f"{year}-06-01Z",
            "paaasiallinen_kayttotarkoitus": purpose,
            "kunta": kunta,
            "elinkaaren_tila": "Käytössä",
            "julkisivumateriaali": facade,
            "lammitystapa": "Vesikeskuslämmitys",
            "lammitysenergian_lahde": energy,
            "rakentamistapa": method,
            "kerrosluku": 4,
            "kerrosala": 2000,
            "huoneistojen_lukumaara": apartments,
            "aikaleima_utc": stamp,
        },
        "geometry": {"type": "Point", "coordinates": [23.76, 61.49]},
    }


def _address(building_key, street, number, kunta_code="837", postal="33100"):
    return {
        "properties": {
            "building_key": building_key,
            "address_fin": f"{street} {number}",
            "address_name_fin": street,
            "number_part_of_address_number": number,
            "number_part_of_address_number2": None,
            "address_number": 1,
            "postal_code": postal,
            "postal_office_fin": "TAMPERE",
            "municipality_number": kunta_code,
        }
    }


def _company(business_id, name, street, number, city):
    return {
        "businessId": {"value": business_id, "registrationDate": "1975-01-01"},
        "names": [{"name": name, "type": "1", "registrationDate": "1975-01-01"}],
        "addresses": [
            {
                "type": 2,
                "street": street,
                "buildingNumber": str(number),
                "postCode": "33100",
                "postOffices": [{"city": city.upper(), "languageCode": "1"}],
            }
        ],
        "tradeRegisterStatus": "1",
        "companySituations": [],
        "lastModified": "2026-08-12T09:44:19",
    }


BUILDINGS = [
    # oil-heated 1975 block: the name of its company holds the street address,
    # but the company is registered at a property manager's office in Nokia
    _building("k-harmala", "100000001A", "Tampere", "Kerrostalo", 1975, energy="Kevyt polttoöljy"),
    # terraced houses linked by the company's own registered address
    _building("k-koivu", "100000002B", "Tampere", "Pientalo", 1990, apartments=6),
    # 1968 concrete element block with an open alteration permit
    _building("k-kauppa", "100000003C", "Tampere", "Kerrostalo", 1968, facade="Betoni", method="Elementti"),
    # the building at the office address shared by two companies: no link
    _building("k-office", "100000004D", "Tampere", "Kerrostalo", 2005),
]
ADDRESSES = [
    _address("k-harmala", "Härmälänkatu", 13),
    _address("k-koivu", "Koivukuja", 4),
    _address("k-kauppa", "Kauppakatu", 5),
    _address("k-office", "Hatanpään valtatie", 1),
]
PERMITS = [
    {
        "properties": {
            "rakennusavain": "permit-stage-key",
            "pysyva_rakennustunnus": "100000003C",
            "pysyva_lupatunnus": "LUPA-1",
            "lupapaatoksen_kunta": "Tampere",
            "paatospaivamaara": "2026-05-01Z",
            "toimenpiteen_laji": "Muu muutostyö",
            "toimenpiteen_tila": "Suunniteltu",
            "paaasiallinen_kayttotarkoitus": "Kerrostalo",
            "aikaleima_utc": "2026-11-11T21:30:00Z",
        }
    }
]
COMPANIES = {
    "Tampere": [
        _company("1111111-1", "Asunto Oy Härmälänkatu 13", "Välimäenkatu", 23, "Nokia"),
        _company("2222222-2", "As Oy Koivupiha", "Koivukuja", 4, "Tampere"),
        _company("3333333-3", "As Oy Hatanpään Helmi", "Hatanpään valtatie", 1, "Tampere"),
        _company("4444444-4", "As Oy Hatanpään Kulma", "Hatanpään valtatie", 1, "Tampere"),
    ]
}


class FakeApi:
    """Answers the Ryhti and PRH URLs from canned rows, with paging."""

    def __init__(self, *, fail_on: str | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on

    def __call__(self, url: str, params: dict) -> dict:
        self.calls.append((url, dict(params)))
        if self.fail_on and self.fail_on in url:
            raise RegistryFetchError(f"{url}: HTTP 503")
        if "opendata-ytj-api" in url:
            rows = COMPANIES.get(params["location"], [])
            page = int(params.get("page", 1))
            return {"totalResults": len(rows), "companies": rows[(page - 1) * 100 : page * 100]}
        if "open_address" in url:
            keys = set(re.findall(r"'([^']+)'", params["filter"]))
            rows = [a for a in ADDRESSES if a["properties"]["building_key"] in keys]
        elif "avoimet_lupa_rakennukset" in url:
            rows = PERMITS
        else:
            rows = BUILDINGS
            since = re.search(r"aikaleima_utc>'([^']+)'", params["filter"])
            if since:
                rows = [b for b in rows if b["properties"]["aikaleima_utc"] > since.group(1)]
        start, limit = int(params.get("startIndex", 0)), int(params["limit"])
        return {"features": rows[start : start + limit]}


@pytest.fixture
def api(app):
    app.config["REGISTRY_MUNICIPALITIES"] = "Tampere"
    yield FakeApi()
    app.config["REGISTRY_MUNICIPALITIES"] = ""


def _sync(api):
    return run_registry_sync(ryhti=RyhtiClient(api, page_size=2), prh=PrhClient(api), now=NOW)


def _signals(key):
    building = RegistryBuilding.query.filter_by(building_key=key).one()
    return {(s.kind, s.renovation_type): s for s in RegistrySignal.query.filter_by(building_id=building.id)}


def test_full_sync_links_buildings_and_computes_signals(app, api):
    with app.app_context():
        result = _sync(api)

        assert result["buildings"] == 4 and result["permits"] == 1 and result["addresses"] == 4
        assert RegistryBuilding.query.filter_by(building_key="k-koivu").one().building_type == "Rivitalo"
        assert RegistryHousingCompany.query.count() == 4

        links = {b.building_key: (b.housing_company.name if b.housing_company else None, b.link_method) for b in RegistryBuilding.query}
        assert links["k-harmala"] == ("Asunto Oy Härmälänkatu 13", "name")  # registered in Nokia, found by name
        assert links["k-koivu"] == ("As Oy Koivupiha", "prh_address")
        assert links["k-kauppa"] == (None, None)
        assert links["k-office"] == (None, None)  # two companies share the office address

        harmala = _signals("k-harmala")
        assert harmala[("heating", "Lämmitysjärjestelmän remontti")].confidence == 50
        assert harmala[("age", "Putkiremontti")].due_year == 2026  # 1975 + 50 is past: shown as now

        kauppa = _signals("k-kauppa")
        permit = kauppa[("permit", "Muu remontti")]
        assert permit.confidence == 70 and "01.05.2026" in permit.reason
        assert kauppa[("age", "Julkisivuremontti")].confidence == 45  # concrete element facade
        assert ("age", "Katto- ja vesikattoremontti") not in kauppa  # 1968 + 35 = 2003: window passed

        assert _signals("k-office") == {}  # built 2005: nothing due yet


def test_nothing_reaches_customers(app, api):
    with app.app_context():
        _sync(api)
        assert RegistrySignal.query.count() > 0
        assert Listing.query.count() == 0 and ListingMatch.query.count() == 0


def test_second_run_is_a_delta_and_signals_are_replaced(app, api):
    with app.app_context():
        _sync(api)
        building_calls = [p for url, p in api.calls if "avoimet_rakennukset" in url]
        assert "aikaleima_utc>" not in building_calls[0]["filter"]

        api.calls.clear()
        _sync(api)
        building_calls = [p for url, p in api.calls if "avoimet_rakennukset" in url]
        assert "aikaleima_utc>'2026-11-11T10:00:00Z'" in building_calls[0]["filter"]
        assert not [1 for url, _ in api.calls if "open_address" in url]  # addresses already fetched

        # the oil boiler is gone: the heating signal is removed on recompute
        RegistryBuilding.query.filter_by(building_key="k-harmala").one().heating_source = "Maalämpö tms."
        db.session.commit()
        compute_signals(now=NOW)
        assert ("heating", "Lämmitysjärjestelmän remontti") not in _signals("k-harmala")


def test_a_failed_run_keeps_the_cursor(app, api):
    with app.app_context():
        failing = FakeApi(fail_on="avoimet_rakennukset")
        assert sync_buildings(RyhtiClient(failing, page_size=2), now=NOW) == 0
        row = RegistrySyncState.query.filter_by(name="ryhti_buildings:Tampere").one()
        assert row.cursor is None and "503" in row.last_error and row.last_success_at is None


def test_completed_permit_is_not_a_signal(app, api):
    with app.app_context():
        _sync(api)
        RegistryPermit.query.one().action_state = "Valmistunut"
        db.session.commit()
        compute_signals(now=NOW)
        assert ("permit", "Muu remontti") not in _signals("k-kauppa")


def test_name_address_keys():
    assert name_address_keys("Asunto Oy Armas Lindgrenintie 19") == ["armaslindgrenintie19", "lindgrenintie19"]
    assert name_address_keys("Asunto Oy. Rinnekatu 33 a")[0] == "rinnekatu33"
    assert name_address_keys("As Oy Koivupiha") == []
    assert address_key("Armas Lindgrenin tie", 19) == "armaslindgrenintie19"


def test_linking_is_skipped_when_the_address_is_in_many_towns(app):
    with app.app_context():
        for key, town in (("a", "Tampere"), ("b", "Turku")):
            db.session.add(RegistryBuilding(building_key=key, building_type="Kerrostalo", municipality=town, address_keys=["koulukatu5"]))
        db.session.add(RegistryHousingCompany(business_id="5555555-5", name="As Oy Koulukatu 5", municipality="Helsinki"))
        db.session.commit()
        link_companies()
        assert RegistryBuilding.query.filter(RegistryBuilding.housing_company_id.isnot(None)).count() == 0


def test_registry_page_is_superadmin_only(app, client, api):
    with app.app_context():
        _sync(api)
        admin = create_user("root-registry@test.com", PASSWORD, role="superadmin", organization_id=None)
        admin.totp_enabled = True
        org = create_organization("Asiakas Oy", "asiakas-registry")
        db.session.flush()
        create_user("admin-registry@test.com", PASSWORD, role="admin", organization_id=org.id)
        db.session.commit()

    client.post("/auth/login", data={"email": "admin-registry@test.com", "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    assert client.get("/admin/registry").status_code in (302, 403)
    client.get("/auth/logout")

    client.post("/auth/login", data={"email": "root-registry@test.com", "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    page = client.get("/admin/registry?kunta=Tampere&tyyppi=permit")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Julkiset rekisterit" in html and "Muutostyölupa myönnetty 01.05.2026" not in html
    assert "Muutostyölupa (Muu muutostyö) myönnetty 01.05.2026" in html


def test_scheduler_job_only_when_enabled(app):
    from app.scheduler import register_scheduler_jobs

    class Recorder:
        def __init__(self):
            self.ids = []

        def add_job(self, _func, _trigger, id, **_kwargs):  # noqa: A002
            self.ids.append(id)

    off = Recorder()
    register_scheduler_jobs(off, app)
    assert "registry_sync" not in off.ids

    app.config["REGISTRY_SYNC_ENABLED"] = True
    try:
        on = Recorder()
        register_scheduler_jobs(on, app)
        assert "registry_sync" in on.ids
    finally:
        app.config["REGISTRY_SYNC_ENABLED"] = False


# --- linking rules -------------------------------------------------------------


def _b(key, town="Tampere", keys=(), property_id=None):
    building = RegistryBuilding(
        building_key=key, building_type="Kerrostalo", municipality=town, address_keys=list(keys), property_id=property_id
    )
    db.session.add(building)
    return building


def _c(business_id, name, town="Tampere", street=None):
    company = RegistryHousingCompany(business_id=business_id, name=name, municipality=town, street=street)
    db.session.add(company)
    return company


def _links():
    return {
        b.building_key: (b.housing_company.name if b.housing_company else None, b.link_method)
        for b in RegistryBuilding.query
    }


def test_name_with_a_number_range_and_swedish_street_names():
    from app.registry.sync import _keys_for_address

    assert name_address_keys("As Oy Koulukatu 5-7")[:3] == ["koulukatu5", "koulukatu6", "koulukatu7"]
    keys = _keys_for_address(
        {"address_name_fin": "Mannerheimintie", "address_name_swe": "Mannerheimvägen", "number_part_of_address_number": 5}
    )
    assert keys == ["mannerheimintie5", "mannerheimvagen5"]


def test_street_name_company_links_buildings_on_one_property(app):
    with app.app_context():
        _b("v1", keys=["valkoselantie8"], property_id="P1")
        _b("v2", keys=["valkoselantie10"], property_id="P1")
        _b("m1", keys=["mantytie1"], property_id="P2")
        _b("m2", keys=["mantytie3"], property_id="P3")  # two properties: ambiguous
        _c("6000000-1", "As Oy Valkoseläntie")
        _c("6000000-2", "As Oy Mäntytie")
        db.session.commit()
        link_companies()
        links = _links()
        assert links["v1"] == links["v2"] == ("As Oy Valkoseläntie", "name_street")
        assert links["m1"] == links["m2"] == (None, None)


def test_other_buildings_on_the_property_follow_a_linked_one(app):
    with app.app_context():
        _b("a", keys=["harmalankatu13"], property_id="P9")
        _b("b", keys=["sivukatu2"], property_id="P9")
        _c("7000000-1", "Asunto Oy Härmälänkatu 13")
        db.session.commit()
        link_companies()
        assert _links()["b"] == ("Asunto Oy Härmälänkatu 13", "property")


def test_a_catalogue_listing_names_the_company_of_its_address(app):
    with app.app_context():
        _b("l", keys=["listakatu3"])
        _c("8000000-1", "As Oy Listahovi", town="Nokia")  # managed from another town
        db.session.add(
            Listing(
                source="import", external_id="x1", maakunta="Pirkanmaa", kaupunki="Tampere",
                osoite="Listakatu 3 A 1", taloyhtio="Asunto Oy Listahovi",
            )
        )
        db.session.commit()
        link_companies()
        assert _links()["l"] == ("As Oy Listahovi", "listing")


def test_a_town_in_the_name_beats_the_registered_office_address(app):
    from app.registry.sync import name_town

    assert name_town("As Oy Kangasalan Pikontie 2") == "kangasala"
    assert name_town("As Oy Koivupiha") is None
    with app.app_context():
        _b("kang", town="Kangasala", keys=["pikontie2"])
        _b("tre", town="Tampere", keys=["pikontie2"])
        _b("office", town="Tampere", keys=["vallerinkatu21"])
        _c("9000000-1", "As Oy Kangasalan Pikontie 2", town="Tampere")
        # the only company at a Tampere address, but its name says Hämeenkyrö
        _c("9000000-2", "Asunto Oy Hämeenkyrön Kirkkokatu 11", town="Tampere", street="Vallerinkatu 21 A")
        db.session.commit()
        link_companies()
        links = _links()
        assert links["kang"] == ("As Oy Kangasalan Pikontie 2", "name")
        assert links["tre"] == (None, None)
        assert links["office"] == (None, None)
