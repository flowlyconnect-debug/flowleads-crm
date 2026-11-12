"""Kohteet on a map: positions, optional geocoding and the map page."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.listings import geo
from app.listings import services as svc
from app.listings.import_services import import_listings, parse_listing_csv
from app.listings.models import Listing, ListingRenovation
from app.search.models import SearchProfile
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
PIPE = "Putkiremontti"
NOW = datetime.now(timezone.utc)


def _org(slug):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    user.totp_enabled = True
    db.session.add(
        SearchProfile(
            organization_id=org.id,
            name="P",
            remonttityyppi=PIPE,
            regions=["Uusimaa", "Pirkanmaa"],
            source="oikotie",
            is_active=True,
            min_confidence=50,
            needs_rematch=False,
        )
    )
    db.session.commit()
    return org, user


def _listing(ext, *, city="Espoo", region="Uusimaa", **fields):
    fields.setdefault("taloyhtio", f"As Oy Kartta {ext}")
    listing = Listing(
        source="import",
        external_id=str(ext),
        maakunta=region,
        kaupunki=city,
        osoite=f"Tie {ext}",
        analysis_status="done",
        first_seen_at=NOW,
        last_seen_at=NOW,
        published_at=NOW,
        **fields,
    )
    listing.renovations.append(ListingRenovation(remonttityyppi=PIPE, confidence=80, timing="2027", timing_year=2027))
    db.session.add(listing)
    db.session.flush()
    svc.match_listing(listing)
    db.session.commit()
    return listing


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def test_position_prefers_source_then_town_then_region(app):
    with app.app_context():
        exact = _listing(1, lat=60.2, lon=24.7, geo_precision="source")
        town = _listing(2, city="Tampere", region="Pirkanmaa")
        unknown_town = _listing(3, city="Pikkukylä", region="Pirkanmaa")
        swedish = _listing(4, city="Esbo")
        assert geo.position(exact) == {"lat": 60.2, "lon": 24.7, "precision": "source"}
        where = geo.position(town)
        assert where["precision"] == "city" and abs(where["lat"] - 61.4978) < 0.02 and abs(where["lon"] - 23.761) < 0.04
        assert geo.position(town) == where  # stable across page loads
        assert geo.position(unknown_town)["precision"] == "region"
        assert geo.position(swedish)["precision"] == "city"
        nowhere = Listing(source="import", external_id="x", maakunta="Atlantis")
        assert geo.position(nowhere) is None


def test_valid_position_rejects_points_outside_finland():
    assert geo.valid_position("60,17", "24,94") == (60.17, 24.94)
    assert geo.valid_position(48.85, 2.35) is None  # Paris
    assert geo.valid_position("abc", 1) is None


def test_import_reads_coordinates(app):
    with app.app_context():
        _org("importgeo")
        data = (
            "taloyhtio;kaupunki;maakunta;remonttityyppi;leveysaste;pituusaste\n"
            "As Oy Piste;Espoo;Uusimaa;Putkiremontti;60,2055;24,6559\n"
            "As Oy Pariisi;Espoo;Uusimaa;Putkiremontti;48.85;2.35\n"
        ).encode()
        import_listings(parse_listing_csv(data))
        good = Listing.query.filter_by(taloyhtio="As Oy Piste").one()
        bad = Listing.query.filter_by(taloyhtio="As Oy Pariisi").one()
        assert (good.lat, good.lon, good.geo_precision) == (60.2055, 24.6559, "source")
        assert bad.lat is None


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeSession:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _FakeResponse(answer)


def test_geocoding_is_off_by_default_and_tries_each_address_once(app):
    with app.app_context():
        _org("geocode")
        first = _listing(1)
        second = _listing(2)
        session = _FakeSession([[{"lat": "60.21", "lon": "24.66"}], []])
        assert geo.geocode_listings(session=session, sleep=lambda s: None) == 0  # disabled
        assert session.calls == []

        app.config["GEOCODING_ENABLED"] = True
        try:
            assert geo.geocode_listings(session=session, sleep=lambda s: None) == 1
            assert len(session.calls) == 2
            assert session.calls[0]["params"]["countrycodes"] == "fi"
            assert session.calls[0]["headers"]["User-Agent"].startswith("FlowLeads-CRM")
            db.session.refresh(first)
            db.session.refresh(second)
            located = first if first.lat else second
            assert (located.lat, located.lon, located.geo_precision) == (60.21, 24.66, "address")
            assert first.geocoded_at and second.geocoded_at
            # nothing is retried
            assert geo.geocode_listings(session=_FakeSession([]), sleep=lambda s: None) == 0

            # a network error leaves the listing for the next run
            third = _listing(3)
            assert geo.geocode_listings(session=_FakeSession([ConnectionError("down")]), sleep=lambda s: None) == 0
            db.session.refresh(third)
            assert third.geocoded_at is None
        finally:
            app.config["GEOCODING_ENABLED"] = False


def test_map_page_shows_the_filtered_targets(client, app):
    with app.app_context():
        org, user = _org("mappage")
        _listing(1, lat=60.2, lon=24.7, geo_precision="source")
        _listing(2, city="Tampere", region="Pirkanmaa")
        other, _ = _org("mappage-other")
        email = user.email
    _login(client, email)
    page = client.get("/targets/map")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "vendor/leaflet-1.9.4/leaflet.js" in html and "cdn" not in html.lower()
    points = json.loads(re.search(r'id="targets-map-data">(.*?)</script>', html, re.S).group(1))
    assert {p["title"] for p in points} == {"As Oy Kartta 1", "As Oy Kartta 2"}
    assert {p["precision"] for p in points} == {"source", "city"}
    assert "1 on sijoitettu kunnan tai maakunnan keskustaan" in html

    filtered = client.get("/targets/map?region=Pirkanmaa").get_data(as_text=True)
    points = json.loads(re.search(r'id="targets-map-data">(.*?)</script>', filtered, re.S).group(1))
    assert [p["title"] for p in points] == ["As Oy Kartta 2"]
    # the list links to the map with the same filters
    listing_page = client.get("/targets?region=Pirkanmaa").get_data(as_text=True)
    assert "/targets/map?" in listing_page and "region=Pirkanmaa" in listing_page


def test_map_escapes_listing_text(client, app):
    with app.app_context():
        org, user = _org("mapxss")
        _listing(1, taloyhtio="</script><script>alert(1)</script>")
        email = user.email
    _login(client, email)
    html = client.get("/targets/map").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
