"""Register signals published as "Ennuste" targets (app/registry/publish.py):
off unless REGISTRY_TARGETS_PUBLISH, matched only for organizations with the
"registry_targets" module, and marked as estimates wherever they are shown."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.listings import target_services as targets
from app.listings.models import Listing, ListingMatch
from app.listings.pts import pts_strip
from app.listings.services import rematch_profile
from app.registry.models import RegistryBuilding, RegistrySignal
from app.registry.publish import publish_targets
from app.search.models import SearchProfile
from app.users.services import create_organization, create_user
from tests.test_registry import NOW, FakeApi, _sync

PASSWORD = "securepassword1"
HEATING = "Lämmitysjärjestelmän remontti"


@pytest.fixture
def synced(app):
    app.config["REGISTRY_MUNICIPALITIES"] = "Tampere"
    with app.app_context():
        _sync(FakeApi())  # publishing is off: nothing published yet
    yield
    app.config["REGISTRY_MUNICIPALITIES"] = ""
    app.config["REGISTRY_TARGETS_PUBLISH"] = False


def _org(slug: str, *, estimates: bool, rtype: str = HEATING):
    org = create_organization(f"Org {slug}", slug)
    org.enabled_features = ["registry_targets"] if estimates else []
    db.session.flush()
    user = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    user.totp_enabled = True
    profile = SearchProfile(
        organization_id=org.id,
        name="P",
        remonttityyppi=rtype,
        regions=["Pirkanmaa"],
        source="oikotie",
        is_active=True,
        min_confidence=60,  # above every age estimate: estimates ignore it
        needs_rematch=False,
    )
    db.session.add(profile)
    db.session.commit()
    return org, user, profile


def _publish(app):
    app.config["REGISTRY_TARGETS_PUBLISH"] = True
    return publish_targets(now=NOW)


def test_nothing_is_published_while_switched_off(app, synced):
    with app.app_context():
        assert Listing.query.count() == 0
        assert publish_targets(now=NOW) == {"skipped": True}
        assert Listing.query.count() == 0


def test_published_targets_are_estimates_named_after_the_company(app, synced):
    with app.app_context():
        counts = _publish(app)
        listings = {l.osoite: l for l in Listing.query.filter_by(source="ryhti")}
        # linked buildings with signals, plus the unlinked one with a permit
        assert set(listings) == {"Härmälänkatu 13", "Koivukuja 4", "Kauppakatu 5"}
        assert counts["created"] == 3
        harmala = listings["Härmälänkatu 13"]
        assert harmala.is_estimate and harmala.maakunta == "Pirkanmaa" and harmala.analysis_status == "done"
        assert harmala.taloyhtio == "Asunto Oy Härmälänkatu 13" and harmala.ytunnus == "1111111-1"
        assert {r.remonttityyppi for r in harmala.renovations} >= {HEATING, "Putkiremontti"}
        assert all(r.is_estimate and "ei varmistettu tieto" in r.evidence for r in harmala.renovations)
        # unlinked: only the permit, not the age estimates
        assert [r.remonttityyppi for r in listings["Kauppakatu 5"].renovations] == ["Muu remontti"]
        assert listings["Kauppakatu 5"].taloyhtio is None


def test_matched_only_for_organizations_with_the_module(app, synced):
    with app.app_context():
        without, _, _ = _org("noest", estimates=False)
        with_module, _, _ = _org("est", estimates=True, rtype="Putkiremontti")
        _publish(app)
        assert ListingMatch.query.filter_by(organization_id=without.id).count() == 0
        matches = ListingMatch.query.filter_by(organization_id=with_module.id).all()
        # the age estimate (confidence 30) passes although min_confidence is 60
        assert [m.listing.osoite for m in matches] == ["Härmälänkatu 13"]


def test_a_new_profile_gets_estimates_older_than_the_history_window(app, synced):
    with app.app_context():
        _publish(app)
        org, _, profile = _org("late", estimates=True)
        assert rematch_profile(profile, now=NOW + timedelta(days=120)) == 1
        org2, _, profile2 = _org("late2", estimates=False)
        assert rematch_profile(profile2, now=NOW + timedelta(days=120)) == 0


def test_republish_is_idempotent_and_retires_lost_signals(app, synced):
    with app.app_context():
        org, _, _ = _org("retire", estimates=True)
        _publish(app)
        assert ListingMatch.query.filter_by(organization_id=org.id, status="pending").count() == 1
        assert publish_targets(now=NOW)["unchanged"] == 3

        building = RegistryBuilding.query.filter_by(building_key="k-harmala").one()
        RegistrySignal.query.filter_by(building_id=building.id, kind="heating").delete()
        db.session.commit()
        counts = publish_targets(now=NOW)
        assert counts["updated"] == 1
        assert ListingMatch.query.filter_by(organization_id=org.id, status="pending").count() == 0

        RegistrySignal.query.filter_by(building_id=building.id).delete()
        db.session.commit()
        assert publish_targets(now=NOW)["retired"] == 1
        listing = Listing.query.filter_by(osoite="Härmälänkatu 13").one()
        assert not listing.is_active and listing.renovations == []


def test_kohteet_lead_and_timeline_say_ennuste(app, client, synced):
    with app.app_context():
        org, user, _ = _org("ui", estimates=True)
        _publish(app)
        match = ListingMatch.query.filter_by(organization_id=org.id).one()
        row = targets.target_rows(org.id, [match], {HEATING})[0]
        assert row["estimate"] is True
        strip = pts_strip(match.listing.renovations, now=NOW)
        assert any(m.get("estimate") for cell in strip["years"] for m in cell["marks"])
        email, match_id = user.email, match.id

    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    html = client.get("/targets").get_data(as_text=True)
    assert 'class="target-row__estimate"' in html and "pts__m--estimate" in html

    with app.app_context():
        result = targets.transfer_targets(org.id, [match_id], now=NOW)
        lead = db.session.get(Lead, result["lead_ids"][0])
        assert "Ennuste (Lämmitysjärjestelmän remontti)" in lead.notes
        lead_id = lead.id
    page = client.get(f"/leads/{lead_id}").get_data(as_text=True)
    assert "Miksi kohde löytyi" in page and "ennuste" in page


def test_a_fact_beats_an_estimate_on_the_timeline():
    from types import SimpleNamespace

    fact = SimpleNamespace(remonttityyppi="Putkiremontti", timing_year=NOW.year + 1, is_estimate=False)
    guess = SimpleNamespace(remonttityyppi="Putkiremontti", timing_year=NOW.year + 1, is_estimate=True)
    strip = pts_strip([guess, fact], now=NOW)
    marks = [m for cell in strip["years"] for m in cell["marks"]]
    assert len(marks) == 1 and marks[0]["estimate"] is False
