"""Curated Kohteet: every match is scored and classified (A Kuuma / B Hyvä /
C Seurattava) when it is found, and only the best housing companies of the
allowed classes are released to the customer, a limited number at a time.
Handling targets makes room for the next ones."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.housing import classify, score_target
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.search.models import SearchProfile
from app.users.models import Organization
from app.users.services import create_organization, create_user

NOW = datetime.now(timezone.utc)
PIPE = "Putkiremontti"
PASSWORD = "securepassword1"


@pytest.fixture
def curated(app):
    """Production defaults on a small scale: at most 5 visible, classes A-B."""
    app.config.update(TARGETS_VISIBLE_LIMIT=5, TARGETS_MIN_TIER="B", TARGETS_NEED_LIMIT=50)
    yield
    app.config.update(TARGETS_VISIBLE_LIMIT=0, TARGETS_MIN_TIER="C", TARGETS_NEED_LIMIT=-1)


def _org(slug: str):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    user.totp_enabled = True
    db.session.add(_profile(org.id))
    db.session.commit()
    return org, user


def _profile(org_id: int, *, active: bool = True) -> SearchProfile:
    return SearchProfile(organization_id=org_id, name="P", remonttityyppi=PIPE, regions=["Uusimaa"], source="oikotie",
                         is_active=active, min_confidence=50, needs_rematch=False)


def _batch_org(slug: str, build):
    """The catalogue first, then a new customer's profile: one batch, the way
    onboarding matches a customer (rematch_profile)."""
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    user.totp_enabled = True
    profile = _profile(org.id, active=False)
    db.session.add(profile)
    db.session.commit()
    build()
    profile.is_active = True
    db.session.commit()
    svc.rematch_profile(profile, now=NOW)
    return org, user


QUALITY = {
    # confidence, years ahead (None = no timing), phone, email
    "hot": (95, 1, True, True),
    "good": (70, 3, True, False),
    "weak": (55, None, False, False),
}


def _listing(n: int, quality: str, *, company: str | None = None, confidence: int | None = None) -> Listing:
    conf, ahead, phone, email = QUALITY[quality]
    listing = Listing(
        source="import", external_id=f"r{n}", maakunta="Uusimaa", kaupunki="Espoo", osoite=f"Testitie {n}",
        taloyhtio=company or f"As Oy {quality} {n}", analysis_status="done", first_seen_at=NOW, last_seen_at=NOW,
        published_at=NOW - timedelta(hours=n), isannoitsija="Isännöinti Oy",
        isannoitsija_puh="040 123 4567" if phone else None, isannoitsija_email="info@isannointi.fi" if email else None,
    )
    db.session.add(listing)
    db.session.flush()
    db.session.add(
        ListingRenovation(listing_id=listing.id, remonttityyppi=PIPE, confidence=confidence or conf,
                          timing=str(NOW.year + ahead) if ahead is not None else None,
                          timing_year=NOW.year + ahead if ahead is not None else None)
    )
    db.session.flush()
    svc.match_listing(listing, now=NOW)
    db.session.commit()
    return listing


def _visible(org_id: int) -> list[Listing]:
    return [m.listing for m in targets.list_targets(org_id, filters={"sort": "newest"}, per_page=100, segment="main").items]


def test_classes(app):
    def scored(conf, ahead, phone, email, *, estimate=False):
        listing = Listing(isannoitsija_puh="040" if phone else None, isannoitsija_email="a@b.fi" if email else None,
                          published_at=NOW, first_seen_at=NOW)
        listing.renovations = [ListingRenovation(remonttityyppi=PIPE, confidence=conf,
                                                 timing_year=NOW.year + ahead if ahead is not None else None)]
        return classify(score_target([listing], {PIPE}, now=NOW), estimate=estimate)

    assert scored(*QUALITY["hot"]) == "A"
    assert scored(95, 1, False, False) == "B"  # no contact: not hot
    assert scored(95, 4, True, True) == "B"  # not within two years: not hot
    assert scored(*QUALITY["good"]) == "B"
    assert scored(*QUALITY["weak"]) == "T"  # no decision, no plan, no year: only a need
    assert scored(*QUALITY["hot"], estimate=True) == "B"  # an estimate is never hot


def test_only_the_best_allowed_companies_are_visible(app, curated):
    with app.app_context():
        def build():
            for n in range(4):
                _listing(n, "weak")
            for n in range(4, 10):
                _listing(n, "good", confidence=60 + 4 * (n - 4))  # 60..80: later ones are better
            for n in range(10, 12):
                _listing(n, "hot")

        org, _ = _batch_org("gate", build)
        visible = _visible(org.id)
        assert len(visible) == 5 and targets.count_targets(org.id) == 5
        names = {l.taloyhtio for l in visible}
        assert {"As Oy hot 10", "As Oy hot 11", "As Oy good 9", "As Oy good 8", "As Oy good 7"} == names
        assert not any(name.startswith("As Oy weak") for name in names)  # a need is never on the main list
        matches = ListingMatch.query.filter_by(organization_id=org.id).all()
        assert all(m.tier and m.score is not None for m in matches)
        assert targets.waiting_summary(org.id) == {"queued": 3, "below": 0}
        assert targets.count_targets(org.id, segment="need") == 4  # on the Tarve tab instead


def test_handling_targets_makes_room_for_the_next_best(app, curated):
    with app.app_context():
        org, _ = _org("refill")
        for n in range(8):
            _listing(n, "good", confidence=60 + 4 * n)
        visible = _visible(org.id)
        assert len(visible) == 5  # the first five to arrive (60..76)
        best_waiting = "As Oy good 7"  # confidence 88: the best of the three waiting (80, 84, 88)
        assert best_waiting not in {l.taloyhtio for l in visible}

        first = ListingMatch.query.filter_by(organization_id=org.id, listing_id=visible[0].id).one()
        targets.dismiss_targets(org.id, [first.id])
        assert best_waiting in {l.taloyhtio for l in _visible(org.id)}
        assert targets.count_targets(org.id) == 5

        second = ListingMatch.query.filter_by(organization_id=org.id, listing_id=_visible(org.id)[0].id).one()
        targets.transfer_targets(org.id, [second.id], now=NOW)
        assert targets.count_targets(org.id) == 5
        assert targets.waiting_summary(org.id)["queued"] == 1  # 8 found, 5 visible, 2 handled


def test_another_listing_of_a_visible_company_joins_without_a_slot(app, curated):
    with app.app_context():
        org, _ = _org("join")
        for n in range(5):
            _listing(n, "good")
        _listing(9, "weak", company="As Oy good 0")  # class C alone, but the company is visible
        new = Listing.query.filter_by(external_id="r9").one()
        match = ListingMatch.query.filter_by(listing_id=new.id).one()
        assert match.released_at is not None and targets.count_targets(org.id) == 5


def test_new_targets_count_from_release_not_from_match(app, curated):
    with app.app_context():
        org, _ = _org("digest")
        for n in range(7):
            _listing(n, "good")
        later = NOW + timedelta(hours=2)
        assert targets.count_new_targets(org.id, NOW - timedelta(minutes=1)) == 5
        assert targets.count_new_targets(org.id, later) == 0
        first = ListingMatch.query.filter_by(organization_id=org.id).filter(ListingMatch.released_at.isnot(None)).first()
        targets.dismiss_targets(org.id, [first.id])
        # the next one is released now: it is new although its match is older
        assert targets.count_new_targets(org.id, NOW + timedelta(seconds=1)) >= 1


def test_superadmin_sets_the_limit_and_class(app, client, curated):
    with app.app_context():
        org, _ = _org("admin-set")
        for n in range(3):
            _listing(n, "weak")
        for n in range(3, 10):
            _listing(n, "good")
        assert targets.count_targets(org.id) == 5
        root = create_user("root-release@test.com", PASSWORD, role="superadmin", organization_id=None)
        root.totp_enabled = True
        db.session.commit()
        org_id = org.id
    client.post("/auth/login", data={"email": "root-release@test.com", "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    page = client.get("/admin/customers").get_data(as_text=True)
    assert "2 jonossa, 0 alle luokan" in page
    client.post(f"/admin/customers/{org_id}/targets-visibility", data={"limit": "0", "min_tier": "C"})
    with app.app_context():
        organization = db.session.get(Organization, org_id)
        assert organization.targets_visible_limit == 0 and organization.targets_min_tier == "C"
        assert targets.count_targets(org_id) == 7 and targets.count_targets(org_id, segment="need") == 3


def test_a_clearly_better_target_replaces_the_weakest_after_a_few_days(app, curated):
    with app.app_context():
        org, _ = _org("swap")
        for n in range(5):
            _listing(n, "good", confidence=60 + n)
        _listing(7, "hot")  # list full, everything visible for minutes: it waits
        assert "As Oy hot 7" not in {l.taloyhtio for l in _visible(org.id)}

        ListingMatch.query.filter_by(organization_id=org.id).filter(ListingMatch.released_at.isnot(None)).update(
            {"released_at": NOW - timedelta(days=4)}
        )
        db.session.commit()
        targets.release_targets(org.id, now=NOW)
        db.session.commit()
        names = {l.taloyhtio for l in _visible(org.id)}
        assert "As Oy hot 7" in names and "As Oy good 0" not in names  # the weakest went back to the queue
        assert targets.count_targets(org.id) == 5
        assert targets.waiting_summary(org.id)["queued"] == 1


def test_a_slightly_better_target_does_not_replace_anything(app, curated):
    with app.app_context():
        org, _ = _org("noswap")
        for n in range(5):
            _listing(n, "good", confidence=60)
        _listing(7, "good", confidence=64)  # same class, a few points better
        ListingMatch.query.filter_by(organization_id=org.id).filter(ListingMatch.released_at.isnot(None)).update(
            {"released_at": NOW - timedelta(days=10)}
        )
        db.session.commit()
        targets.release_targets(org.id, now=NOW)
        assert "As Oy good 7" not in {l.taloyhtio for l in _visible(org.id)}


def test_kohteet_tells_how_many_wait(app, client, curated):
    with app.app_context():
        def build():
            for n in range(6):
                _listing(n, "good")
            _listing(7, "hot")

        org, user = _batch_org("page", build)
        email = user.email
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    html = client.get("/targets").get_data(as_text=True)
    assert "5 taloyhtiötä sopii profiiliisi" in html and "on päätetty tai suunniteltu" in html
    assert "Lisäksi 2 odottaa" in html
