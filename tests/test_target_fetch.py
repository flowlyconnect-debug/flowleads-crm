""""Hae kohteita": new matches wait in a reserve and the customer releases a
batch with the button, at most once per hour."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.notifications.models import Notification
from app.search.models import SearchProfile
from app.users.models import AuditLog
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
PIPE = "Putkiremontti"
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def fetch_mode(app):
    app.config["TARGET_FETCH_ENABLED"] = True
    yield
    app.config["TARGET_FETCH_ENABLED"] = False


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


def _listing(ext: int) -> Listing:
    listing = Listing(
        source="import",
        external_id=str(ext),
        maakunta="Uusimaa",
        kaupunki="Espoo",
        osoite=f"Testitie {ext}",
        taloyhtio=f"As Oy Kohde {ext}",
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


def _reserve(org, count: int, start: int = 1) -> None:
    """Listings matched the way the app does it (match_listing)."""
    for ext in range(start, start + count):
        svc.match_listing(_listing(ext), now=NOW)
    db.session.commit()


def _login(client, email: str):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def test_new_matches_wait_in_the_reserve(app):
    with app.app_context():
        org, _, _ = _org("reserve")
        _reserve(org, 5)
        assert ListingMatch.query.filter_by(organization_id=org.id).count() == 5
        assert targets.count_targets(org.id) == 0  # nothing on Kohteet, the badge or the dashboard
        assert targets.count_reserve(org.id) == 5


def test_fetch_releases_a_varying_batch_after_the_wait(app):
    with app.app_context():
        org, _, _ = _org("batch")
        _reserve(org, 40)
        sizes = set()
        now = NOW
        for round_number in range(6):
            now = now + timedelta(hours=2)
            result = targets.fetch_targets(org.id, now=now, rng=random.Random(round_number))
            db.session.commit()
            assert 3 <= result["count"] <= 8
            assert 10 <= result["wait_seconds"] <= 15
            sizes.add(result["count"])
            # released into the future: invisible until the animation ends
            pending = ListingMatch.query.filter(ListingMatch.released_at > now).count()
            assert pending == result["count"]
        assert len(sizes) > 1, "the batch size must vary between fetches"

        released = ListingMatch.query.filter(ListingMatch.released_at.isnot(None)).all()
        assert len(released) + targets.count_reserve(org.id) == 40
        # newest listings go first: listing N was published N minutes ago
        assert sorted(int(m.listing.external_id) for m in released) == list(range(1, len(released) + 1))


def test_released_targets_appear_only_when_the_wait_is_over(app, monkeypatch):
    with app.app_context():
        org, _, _ = _org("wait")
        _reserve(org, 4)
        result = targets.fetch_targets(org.id, now=NOW, rng=random.Random(1))
        db.session.commit()
        monkeypatch.setattr(targets, "_now", lambda: NOW + timedelta(seconds=5))
        assert targets.count_targets(org.id) == 0
        monkeypatch.setattr(targets, "_now", lambda: NOW + timedelta(seconds=result["wait_seconds"] + 1))
        assert targets.count_targets(org.id) == result["count"]


def test_one_fetch_per_hour_per_organization(app):
    with app.app_context():
        org, _, _ = _org("cooldown")
        other, _, _ = _org("cooldown2")
        _reserve(org, 30)
        targets.fetch_targets(org.id, now=NOW)
        db.session.commit()
        with pytest.raises(targets.TargetFetchCooldown) as error:
            targets.fetch_targets(org.id, now=NOW + timedelta(minutes=59))
        assert error.value.next_fetch_at == NOW + timedelta(minutes=60)
        # another customer is not affected, and the hour does pass
        targets.fetch_targets(other.id, now=NOW + timedelta(minutes=1))
        assert targets.fetch_targets(org.id, now=NOW + timedelta(minutes=61))["count"] >= 3


def test_empty_reserve_finds_nothing_and_still_starts_the_cooldown(app):
    with app.app_context():
        org, _, _ = _org("empty")
        assert targets.fetch_targets(org.id, now=NOW)["count"] == 0
        db.session.commit()
        with pytest.raises(targets.TargetFetchCooldown):
            targets.fetch_targets(org.id, now=NOW + timedelta(minutes=5))


def test_fetch_button_endpoint(client, app):
    with app.app_context():
        org, user, _ = _org("button", role="user")
        _reserve(org, 12)
        email, org_id = user.email, org.id
    _login(client, email)

    page = client.get("/targets").get_data(as_text=True)
    assert 'id="targets-fetch-btn"' in page and "Hae kohteita" in page
    assert "As Oy Kohde" not in page  # the reserve is not listed
    assert "varastossa" not in page  # the reserve count is for the platform's superadmin only

    response = client.post("/targets/fetch")
    data = response.get_json()
    assert response.status_code == 200 and data["success"]
    assert 3 <= data["count"] <= 8 and 10 <= data["wait_seconds"] <= 15
    assert f"fetched={data['count']}" in data["redirect"]

    again = client.post("/targets/fetch")
    assert again.status_code == 429
    assert "kerran tunnissa" in again.get_json()["message"]

    with app.app_context():
        # make the wait pass
        ListingMatch.query.filter(ListingMatch.released_at.isnot(None)).update(
            {"released_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )
        db.session.commit()
        assert AuditLog.query.filter_by(action="targets_fetched", organization_id=org_id).count() == 1
    page = client.get(data["redirect"]).get_data(as_text=True)
    assert page.count('class="target-row"') == data["count"]
    assert "uutta kohdetta" in page and "badge-new" in page
    assert "Seuraava haku klo" in page and "disabled" in page


def test_digest_points_to_the_button_instead_of_listing_numbers(app):
    with app.app_context():
        org, user, _ = _org("digestfetch")
        _reserve(org, 3)
        assert targets.send_target_digests(now=NOW + timedelta(minutes=5)) == 1
        note = Notification.query.filter_by(user_id=user.id).one()
        assert note.title == "Uusia kohteita haettavissa"
        assert "Hae kohteita" in note.message
        # nothing new in the reserve since -> silent
        assert targets.send_target_digests(now=NOW + timedelta(hours=24)) == 0


def test_feature_off_shows_matches_at_once(app):
    app.config["TARGET_FETCH_ENABLED"] = False
    with app.app_context():
        org, _, _ = _org("off")
        _reserve(org, 3)
        assert targets.count_targets(org.id) == 3
