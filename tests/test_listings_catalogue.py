"""Shared Oikotie listing catalogue: crawl -> analyse -> match -> lead -> contacts."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.leads.models import Lead
from app.listings.models import CrawlState, Listing, ListingMatch
from app.listings import services as svc
from app.search.models import SearchProfile
from app.users.services import create_organization

# These tests cover the automatic match -> lead pipeline; the default manual
# flow (Kohteet page) is covered in tests/test_targets.py.
pytestmark = pytest.mark.usefixtures("auto_create_leads")

LOCK = "Lukitus, kulunvalvonta ja oviautomatiikka"
PIPE = "Putki- ja viemäriurakointi"


def _h():
    return {"X-N8N-Secret": "test-n8n-secret", "Content-Type": "application/json"}


def _org(slug):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    return org


def _profile(org, *, regions=("Uusimaa",), rtype=LOCK, min_conf=60, limit=50, active=True, name="P"):
    p = SearchProfile(
        organization_id=org.id,
        name=name,
        remonttityyppi=rtype,
        regions=list(regions),
        source="oikotie",
        is_active=active,
        min_confidence=min_conf,
        daily_lead_limit=limit,
        needs_rematch=False,
    )
    db.session.add(p)
    db.session.flush()
    return p


def _card(i, **extra):
    data = {
        "external_id": str(i),
        "url": f"https://asunnot.oikotie.fi/myytavat-asunnot/espoo/{i}",
        "kaupunki": "Espoo",
        "osoite": f"Testitie {i}",
        "hinta": "200 000 €",
        "alue": "60 m²",
        "description": f"Kohde {i}",
    }
    data.update(extra)
    return data


def _ingest(maakunta, ids, *, mode="incremental", offset=0, page=1, **kw):
    return svc.ingest_page(
        maakunta=maakunta,
        mode=mode,
        offset=offset,
        page_in_run=page,
        listings=[_card(i) for i in ids],
        **kw,
    )


def _analyse(listing, renovations, **fields):
    payload = {"status": "done", "renovations": renovations}
    payload.update(fields)
    return svc.save_analysis(listing.id, payload)


def _listing(ext):
    return Listing.query.filter_by(external_id=str(ext)).one()


# --- crawl plan + ingest ---------------------------------------------------


def test_crawl_plan_has_incremental_for_every_region_and_a_full_slice(app):
    with app.app_context():
        tasks = svc.get_crawl_plan()
        inc = [t for t in tasks if t["mode"] == "incremental"]
        full = [t for t in tasks if t["mode"] == "full"]
        assert len(inc) == 18
        assert {t["maakunta"] for t in inc} >= {"Uusimaa", "Keski-Pohjanmaa", "Lappi"}
        assert len(full) == 2
        assert all(t["offset"] == 0 for t in full)

        # Second plan continues the same in-progress full passes from their cursor.
        state = CrawlState.query.filter_by(maakunta=full[0]["maakunta"]).one()
        state.full_offset = 240
        db.session.commit()
        again = [t for t in svc.get_crawl_plan() if t["mode"] == "full"]
        assert {t["maakunta"] for t in again} == {t["maakunta"] for t in full}
        assert any(t["offset"] == 240 for t in again)


def test_crawl_plan_active_mode_only_crawls_profile_regions(app):
    app.config["LISTING_CRAWL_REGIONS"] = "active"
    try:
        with app.app_context():
            org = _org("active-crawl")
            _profile(org, regions=["Lappi", "Kainuu"])
            _profile(org, regions=["Uusimaa"], active=False, name="off")
            db.session.commit()
            tasks = svc.get_crawl_plan()
            assert {t["maakunta"] for t in tasks} == {"Lappi", "Kainuu"}
    finally:
        app.config["LISTING_CRAWL_REGIONS"] = "all"


def test_incremental_ingest_creates_and_stops_at_known_listings(app):
    with app.app_context():
        first = _ingest("Uusimaa", range(1, 25))
        assert first["created"] == 24
        assert first["continue"] is True
        assert first["next_offset"] == 24

        again = _ingest("Uusimaa", range(1, 25))
        assert again["created"] == 0
        assert again["updated"] == 24
        assert again["continue"] is False  # whole page already known -> caught up

        short = _ingest("Uusimaa", range(100, 105), offset=24, page=2)
        assert short["continue"] is False  # last page
        assert Listing.query.count() == 29
        assert _listing(1).analysis_status == "pending"


def test_incremental_ingest_respects_page_cap(app):
    app.config["LISTING_INCREMENTAL_MAX_PAGES"] = 2
    try:
        with app.app_context():
            assert _ingest("Lappi", range(1, 25), page=2)["continue"] is False
    finally:
        app.config["LISTING_INCREMENTAL_MAX_PAGES"] = 10


def test_changed_description_requeues_analysis(app):
    with app.app_context():
        _ingest("Uusimaa", [7])
        listing = _listing(7)
        listing.analysis_status = "done"
        db.session.commit()

        _ingest("Uusimaa", [7])  # same text: no change
        assert _listing(7).analysis_status == "done"

        svc.ingest_page(
            maakunta="Uusimaa", mode="incremental", offset=0, page_in_run=1,
            listings=[_card(7, description="Lukitus uusitaan 2027")],
        )
        assert _listing(7).analysis_status == "pending"


def test_full_pass_advances_cursor_and_removes_listings_missed_twice(app):
    with app.app_context():
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Pass 1 (backfill) sees listings 1..30.
        state = svc._state("Kainuu")
        state.full_started_at = t0
        db.session.commit()
        r = _ingest("Kainuu", range(1, 25), mode="full", now=t0)
        assert r["continue"] is True
        assert CrawlState.query.filter_by(maakunta="Kainuu").one().full_offset == 24
        r = _ingest("Kainuu", range(25, 31), mode="full", offset=24, page=2, now=t0)
        assert r["continue"] is False
        state = CrawlState.query.filter_by(maakunta="Kainuu").one()
        assert state.full_completed_at is not None and state.full_offset == 0
        assert r["removed"] == 0  # first pass never removes anything

        # Pass 2 a week later: listing 30 is gone. Missed once -> still active.
        t1 = t0 + timedelta(days=8)
        state.prev_full_started_at, state.full_started_at = state.full_started_at, t1
        db.session.commit()
        _ingest("Kainuu", range(1, 25), mode="full", now=t1)
        r = _ingest("Kainuu", range(25, 30), mode="full", offset=24, page=2, now=t1)
        assert r["removed"] == 0
        assert _listing(30).is_active is True

        # Pass 3: still gone -> missed by two passes in a row -> removed.
        t2 = t1 + timedelta(days=8)
        state = CrawlState.query.filter_by(maakunta="Kainuu").one()
        state.prev_full_started_at, state.full_started_at = state.full_started_at, t2
        db.session.commit()
        _ingest("Kainuu", range(1, 25), mode="full", now=t2)
        r = _ingest("Kainuu", range(25, 30), mode="full", offset=24, page=2, now=t2)
        assert r["removed"] == 1
        assert _listing(30).is_active is False
        assert _listing(29).is_active is True

        # It reappears -> reactivated.
        _ingest("Kainuu", [30], now=t2)
        assert _listing(30).is_active is True


def test_ingest_error_is_recorded_and_stops(app):
    with app.app_context():
        r = svc.ingest_page(
            maakunta="Lappi", mode="incremental", offset=0, page_in_run=1, listings=[], error="HTTP 403"
        )
        assert r["continue"] is False
        assert CrawlState.query.filter_by(maakunta="Lappi").one().last_error == "HTTP 403"


# --- analysis queue ---------------------------------------------------------


def test_claim_analysis_is_exclusive_and_retries_stale_claims(app):
    with app.app_context():
        _ingest("Uusimaa", [1, 2, 3])
        first = svc.claim_analysis(limit=2)
        assert len(first["listings"]) == 2
        assert svc.REMONTTITYYPIT[0] in first["remonttityypit"]
        second = svc.claim_analysis(limit=5)
        assert len(second["listings"]) == 1  # the other two are processing

        # A claim older than the timeout is handed out again.
        for l in Listing.query.filter_by(analysis_status="processing").all():
            l.analysis_claimed_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.session.commit()
        assert len(svc.claim_analysis(limit=5)["listings"]) == 3


def test_analysis_gives_up_after_max_attempts(app):
    with app.app_context():
        _ingest("Uusimaa", [1])
        for _ in range(3):
            claimed = svc.claim_analysis(limit=1)["listings"]
            assert claimed
            svc.save_analysis(claimed[0]["listing_id"], {"status": "failed", "error": "AI timeout"})
        listing = _listing(1)
        assert listing.analysis_status == "failed"
        assert listing.analysis_attempts == 3
        assert svc.claim_analysis(limit=1)["listings"] == []


def test_save_analysis_validates_types_and_clamps_confidence(app):
    with app.app_context():
        _ingest("Uusimaa", [1])
        listing = _listing(1)
        result = _analyse(
            listing,
            [
                {"remonttityyppi": LOCK, "confidence": 140, "timing": "2027", "evidence": "iLOQ 2027"},
                {"remonttityyppi": LOCK, "confidence": 50},  # lower duplicate dropped
                {"remonttityyppi": "Saunaremontti", "confidence": 90},  # not a CRM type
                {"remonttityyppi": PIPE, "confidence": "x"},
            ],
            taloyhtio="As Oy Testi",
            isannoitsija="Isännöinti Oy",
        )
        assert result["renovations"] == 2
        listing = _listing(1)
        by_type = {r.remonttityyppi: r for r in listing.renovations}
        assert by_type[LOCK].confidence == 100
        assert by_type[LOCK].evidence == "iLOQ 2027"
        assert by_type[PIPE].confidence == 0
        assert listing.taloyhtio == "As Oy Testi"
        assert listing.analysis_status == "done"


# --- matching -> leads ------------------------------------------------------


def test_analysed_listing_becomes_lead_only_for_matching_profiles(app):
    with app.app_context():
        org_a, org_b = _org("match-a"), _org("match-b")
        want = _profile(org_a, regions=["Uusimaa"], rtype=LOCK, min_conf=60, name="want")
        other_region = _profile(org_a, regions=["Lappi"], rtype=LOCK, name="lappi")
        strict = _profile(org_b, regions=["Uusimaa"], rtype=LOCK, min_conf=90, name="strict")
        other_type = _profile(org_b, regions=["Uusimaa"], rtype=PIPE, name="pipe")
        inactive = _profile(org_b, regions=["Uusimaa"], rtype=LOCK, active=False, name="off")
        whole = _profile(org_b, regions=["Koko Suomi"], rtype=LOCK, name="koko")
        db.session.commit()

        _ingest("Uusimaa", [11])
        result = _analyse(
            _listing(11),
            [{"remonttityyppi": LOCK, "confidence": 80, "evidence": "Lukitus uusitaan"}],
            taloyhtio="As Oy Koski",
            isannoitsija="Isännöinti Lappi Oy",
            isannoitsija_puh="0401234567",
            isannoitsija_email="info@isannointi.fi",
        )
        assert result["matches"] == 2
        assert result["leads_created"] == 2

        matched = {m.profile_id for m in ListingMatch.query.all()}
        assert matched == {want.id, whole.id}
        for p in (other_region, strict, other_type, inactive):
            assert p.id not in matched

        lead_a = Lead.query.filter_by(organization_id=org_a.id).one()
        assert lead_a.source == "oikotie"
        assert lead_a.source_ref == "oikotie-11"
        assert lead_a.company == "Isännöinti Lappi Oy"
        assert lead_a.phone == "0401234567"
        assert lead_a.email == "info@isannointi.fi"
        assert lead_a.gdpr_legal_basis == "legitimate_interest"
        assert lead_a.ai_contact_info["taloyhtio"] == "As Oy Koski"
        assert lead_a.ai_contact_info["ai_confidence"] == 80
        assert LOCK in (lead_a.tags or [])
        # Each organization gets its own lead from the same shared listing.
        assert Lead.query.filter_by(organization_id=org_b.id).count() == 1
        assert db.session.get(SearchProfile, want.id).total_leads_sent == 1
        # Contacts were already on the listing page: no enrichment needed.
        assert _listing(11).contact_status == "none"


def test_daily_lead_limit_defers_extra_matches_to_next_day(app):
    with app.app_context():
        org = _org("limit")
        profile = _profile(org, limit=2)
        db.session.commit()
        now = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
        _ingest("Uusimaa", range(1, 6), now=now)
        for i in range(1, 6):
            svc.save_analysis(
                _listing(i).id,
                {"status": "done", "renovations": [{"remonttityyppi": LOCK, "confidence": 70}]},
                now=now,
            )
        assert Lead.query.filter_by(organization_id=org.id).count() == 2
        assert ListingMatch.query.filter_by(profile_id=profile.id, status="pending").count() == 3

        # Same day: still capped.
        assert svc.materialize_pending_matches(now=now + timedelta(hours=1)) == 0
        # Next day: two more.
        assert svc.materialize_pending_matches(now=now + timedelta(days=1)) == 2
        assert svc.materialize_pending_matches(now=now + timedelta(days=2)) == 1
        assert ListingMatch.query.filter_by(status="pending").count() == 0


def test_unlimited_profile_and_inactive_listing(app):
    with app.app_context():
        org = _org("unlimited")
        _profile(org, limit=0)
        db.session.commit()
        _ingest("Uusimaa", range(1, 4))
        for i in range(1, 4):
            listing = _listing(i)
            if i == 3:
                listing.is_active = False
                db.session.commit()
            _analyse(listing, [{"remonttityyppi": LOCK, "confidence": 99}])
        # inactive listing never matches; the other two become leads at once
        assert Lead.query.filter_by(organization_id=org.id).count() == 2


def test_new_profile_rematch_uses_history_window_and_keeps_created_leads(app):
    with app.app_context():
        now = datetime.now(timezone.utc)
        _ingest("Pirkanmaa", [1, 2, 3])
        for i in (1, 2, 3):
            _analyse(_listing(i), [{"remonttityyppi": PIPE, "confidence": 85}])
        old = _listing(3)
        old.first_seen_at = now - timedelta(days=90)
        db.session.commit()

        org = _org("rematch")
        profile = _profile(org, regions=["Pirkanmaa"], rtype=PIPE)
        profile.needs_rematch = True
        db.session.commit()

        assert svc.rematch_profiles() == 2  # listing 3 is outside the 30-day window
        assert db.session.get(SearchProfile, profile.id).needs_rematch is False
        assert svc.materialize_pending_matches() == 2

        # Filter change: higher threshold. Already-created leads stay.
        profile = db.session.get(SearchProfile, profile.id)
        profile.min_confidence = 95
        profile.needs_rematch = True
        db.session.commit()
        svc.rematch_profiles()
        assert ListingMatch.query.filter_by(profile_id=profile.id, status="lead_created").count() == 2

        # Deactivating the profile drops only pending matches.
        _ingest("Pirkanmaa", [4])
        profile = db.session.get(SearchProfile, profile.id)
        profile.min_confidence = 60
        db.session.commit()
        _analyse(_listing(4), [{"remonttityyppi": PIPE, "confidence": 85}])
        profile = db.session.get(SearchProfile, profile.id)
        profile.daily_lead_limit = 2
        profile.is_active = False
        profile.needs_rematch = True
        db.session.commit()
        svc.rematch_profiles()
        assert ListingMatch.query.filter_by(profile_id=profile.id, status="pending").count() == 0
        assert ListingMatch.query.filter_by(profile_id=profile.id, status="lead_created").count() >= 2


def test_deleted_lead_is_not_recreated(app):
    with app.app_context():
        org = _org("deleted")
        profile = _profile(org)
        db.session.commit()
        _ingest("Uusimaa", [1])
        _analyse(_listing(1), [{"remonttityyppi": LOCK, "confidence": 90}])
        lead = Lead.query.filter_by(organization_id=org.id).one()
        match = ListingMatch.query.filter_by(profile_id=profile.id).one()
        match.lead_id = None  # what ON DELETE SET NULL leaves behind
        db.session.commit()
        from sqlalchemy import text

        for table in ("activities", "lead_contacts"):
            try:
                db.session.execute(text(f"DELETE FROM {table} WHERE lead_id = :id"), {"id": lead.id})
            except Exception:
                db.session.rollback()
        db.session.execute(text("DELETE FROM leads WHERE id = :id"), {"id": lead.id})
        db.session.commit()
        db.session.expire_all()

        profile = db.session.get(SearchProfile, profile.id)
        profile.needs_rematch = True
        db.session.commit()
        svc.rematch_profiles()
        svc.materialize_pending_matches()
        assert Lead.query.filter_by(organization_id=org.id).count() == 0


def test_profile_delete_removes_its_matches(app):
    from app.search.profile_services import delete_profile

    with app.app_context():
        org = _org("delprof")
        profile = _profile(org, limit=0)
        db.session.commit()
        _ingest("Uusimaa", [1])
        _analyse(_listing(1), [{"remonttityyppi": LOCK, "confidence": 90}])
        delete_profile(db.session.get(SearchProfile, profile.id))
        db.session.commit()
        assert ListingMatch.query.count() == 0


# --- contacts ---------------------------------------------------------------


def test_matched_listing_without_contacts_is_enriched_and_lead_updated(app):
    with app.app_context():
        org = _org("contacts")
        _profile(org)
        db.session.commit()
        _ingest("Uusimaa", [1, 2])
        _analyse(_listing(1), [{"remonttityyppi": LOCK, "confidence": 90}], taloyhtio="As Oy Ei Yhteystietoja")
        _analyse(_listing(2), [{"remonttityyppi": PIPE, "confidence": 90}])  # no match -> no enrichment
        assert _listing(1).contact_status == "pending"
        assert _listing(2).contact_status == "none"

        lead = Lead.query.filter_by(organization_id=org.id).one()
        assert lead.company == "As Oy Ei Yhteystietoja"
        assert not lead.phone

        claimed = svc.claim_contacts(limit=10)["listings"]
        assert [c["listing_id"] for c in claimed] == [_listing(1).id]
        result = svc.save_contacts(
            _listing(1).id,
            {
                "status": "done",
                "isannoitsija": "Isännöinti Koski Oy",
                "phone": "+358401111111",
                "email": "toimisto@koski.fi",
                "website": "https://koski.fi",
                "source": "finder",
            },
        )
        assert result == {"status": "done", "leads_updated": 1}
        lead = Lead.query.filter_by(organization_id=org.id).one()
        assert lead.phone == "+358401111111"
        assert lead.email == "toimisto@koski.fi"
        assert lead.company == "Isännöinti Koski Oy"
        assert lead.ai_contact_info["yhteystieto_lahde"] == "finder"
        assert svc.claim_contacts(limit=10)["listings"] == []


def test_contacts_not_found(app):
    with app.app_context():
        org = _org("nocontacts")
        _profile(org)
        db.session.commit()
        _ingest("Uusimaa", [1])
        _analyse(_listing(1), [{"remonttityyppi": LOCK, "confidence": 90}])
        claimed = svc.claim_contacts(limit=1)["listings"]
        assert svc.save_contacts(claimed[0]["listing_id"], {"status": "not_found"})["status"] == "not_found"


# --- HTTP API (what n8n calls) ---------------------------------------------


def test_listing_api_requires_n8n_secret(client):
    assert client.get("/api/v1/n8n/listings/crawl-plan").status_code == 401
    bad = {"X-N8N-Secret": "wrong", "Content-Type": "application/json"}
    assert client.post("/api/v1/n8n/listings/analysis/claim", data="{}", headers=bad).status_code == 401


def test_listing_api_end_to_end(client, app):
    with app.app_context():
        org = _org("api-e2e")
        _profile(org, regions=["Lappi"])
        db.session.commit()
        org_id = org.id

    plan = client.get("/api/v1/n8n/listings/crawl-plan", headers=_h()).get_json()["tasks"]
    lappi = next(t for t in plan if t["maakunta"] == "Lappi" and t["mode"] == "incremental")
    assert lappi["slug"] == "lappi"

    resp = client.post(
        "/api/v1/n8n/listings/ingest",
        data=json.dumps({"maakunta": "Lappi", "mode": "incremental", "offset": 0, "page_in_run": 1,
                         "listings": [_card(900), _card(901)]}),
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.get_json()["created"] == 2

    bad = client.post(
        "/api/v1/n8n/listings/ingest",
        data=json.dumps({"maakunta": "Atlantis", "listings": []}),
        headers=_h(),
    )
    assert bad.status_code == 400

    claim = client.post("/api/v1/n8n/listings/analysis/claim", data=json.dumps({"limit": 5}), headers=_h()).get_json()
    assert len(claim["listings"]) == 2
    target = next(l for l in claim["listings"] if l["external_id"] == "900")

    saved = client.post(
        f"/api/v1/n8n/listings/{target['listing_id']}/analysis",
        data=json.dumps({"status": "done", "isannoitsija": "Lapin Isännöinti Oy",
                         "renovations": [{"remonttityyppi": LOCK, "confidence": 77}]}),
        headers=_h(),
    )
    assert saved.status_code == 200
    assert saved.get_json()["leads_created"] == 1

    missing = client.post("/api/v1/n8n/listings/999999/analysis", data=json.dumps({"status": "done"}), headers=_h())
    assert missing.status_code == 404

    contacts = client.post("/api/v1/n8n/listings/contacts/claim", data=json.dumps({}), headers=_h()).get_json()
    assert [c["listing_id"] for c in contacts["listings"]] == [target["listing_id"]]
    done = client.post(
        f"/api/v1/n8n/listings/{target['listing_id']}/contacts",
        data=json.dumps({"status": "done", "phone": "0405555555", "source": "asiakastieto"}),
        headers=_h(),
    )
    assert done.get_json()["leads_updated"] == 1

    with app.app_context():
        lead = Lead.query.filter_by(organization_id=org_id).one()
        assert lead.phone == "0405555555"
        assert lead.company == "Lapin Isännöinti Oy"
