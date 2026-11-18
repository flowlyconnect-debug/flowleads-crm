"""Kohteet and the matching job at catalogue scale (research round 11/2026):
"Sopivimmat ensin" scores only the targets that can reach the page but must
give exactly the order of scoring them all; scores are stored for every new
match; the hourly score refresh is its own scheduler job."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.fit_services import learn_preferences, score_matches
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.search.models import SearchProfile
from app.users.services import create_organization

NOW = datetime.now(timezone.utc)
TYPES = ("Putkiremontti", "Julkisivuremontti")
REGIONS = ("Uusimaa", "Pirkanmaa", "Varsinais-Suomi")


def _catalogue(slug: str, count: int = 80):
    random.seed(7)
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    profile = SearchProfile(
        organization_id=org.id, name="P", remonttityyppi=TYPES[0], remonttityypit=list(TYPES),
        regions=list(REGIONS), source="oikotie", is_active=True, min_confidence=50, needs_rematch=False,
    )
    db.session.add(profile)
    db.session.flush()
    for i in range(count):
        listing = Listing(
            source="import", external_id=f"{slug}-{i}", maakunta=random.choice(REGIONS), kaupunki=f"Kunta {i % 9}",
            osoite=f"Katu {i}", taloyhtio=f"As Oy {slug} {i // 2}",  # two listings per housing company
            analysis_status="done", first_seen_at=NOW, last_seen_at=NOW,
            published_at=NOW - timedelta(days=random.randint(0, 25)), build_year=random.randint(1955, 2005),
            isannoitsija="Isännöinti Oy", isannoitsija_puh="040 1" if i % 3 else None,
        )
        db.session.add(listing)
        db.session.flush()
        for rtype in random.sample(TYPES, random.randint(1, 2)):
            db.session.add(
                ListingRenovation(listing_id=listing.id, remonttityyppi=rtype, confidence=random.randint(50, 95),
                                  timing_year=NOW.year + random.randint(0, 4))
            )
    db.session.commit()
    svc.rematch_profile(profile, now=NOW)
    return org, profile


def _brute_force(org_id: int) -> list[int]:
    """Every visible target scored, the way the page sorted before."""
    matches = ListingMatch.query.filter(ListingMatch.id.in_(targets._visible_match_ids(org_id, "pending"))).all()
    wanted = targets.organization_wanted_types(org_id)
    fits = score_matches(matches, learn_preferences(org_id, wanted), wanted_types=wanted)

    def key(match):
        seen = match.listing.published_at or match.listing.first_seen_at
        return (-fits[match.id][0], -seen.timestamp(), -match.id)

    return [m.id for m in sorted(matches, key=key)]


def _pages(org_id: int, per_page: int) -> list[int]:
    ids, page = [], 1
    while True:
        result = targets.list_targets(org_id, page=page, per_page=per_page)
        ids.extend(m.id for m in result.items)
        if not result.has_next:
            return ids
        page += 1


def test_every_new_match_has_a_stored_score(app):
    with app.app_context():
        org, _ = _catalogue("scored")
        pending = ListingMatch.query.filter_by(organization_id=org.id, status="pending").all()
        assert pending and all(m.score is not None for m in pending)


def _small_batches(monkeypatch, seen: list):
    """Score a few targets at a time so the early stop is exercised."""
    from app.listings import fit_services

    real = fit_services.score_matches

    def counting(batch, *args, **kwargs):
        seen.append(len(list(batch)))
        return real(batch, *args, **kwargs)

    monkeypatch.setattr(targets, "_FIT_BATCH", 4)
    monkeypatch.setattr(fit_services, "score_matches", counting)


def test_fit_order_equals_scoring_every_target(app, monkeypatch):
    seen: list[int] = []
    _small_batches(monkeypatch, seen)
    with app.app_context():
        org, _ = _catalogue("order")
        assert _pages(org.id, 7) == _brute_force(org.id)
        seen.clear()
        targets.list_targets(org.id, page=1, per_page=3)
        total = targets.count_targets(org.id)
        assert sum(seen) < total  # the first page did not score every target


def test_fit_order_with_learned_preferences(app, monkeypatch):
    _small_batches(monkeypatch, [])
    with app.app_context():
        org, _ = _catalogue("learned")
        visible = targets.list_targets(org.id, filters={"sort": "newest"}, per_page=200).items
        # hide a region's targets and move another region's to leads: the bonus leans both ways
        hidden = [m.id for m in visible if m.listing.maakunta == "Uusimaa"][:6]
        moved = [m.id for m in visible if m.listing.maakunta == "Pirkanmaa"][:4]
        targets.dismiss_targets(org.id, hidden)
        targets.transfer_targets(org.id, moved, now=NOW)
        db.session.commit()
        assert learn_preferences(org.id).decisions
        assert _pages(org.id, 5) == _brute_force(org.id)


def test_a_stale_stored_score_is_still_placed_correctly(app, monkeypatch):
    """The hourly refresh may lag: a score a little off must not hide a target."""
    _small_batches(monkeypatch, [])
    with app.app_context():
        org, _ = _catalogue("stale")
        best = _brute_force(org.id)[0]
        match = db.session.get(ListingMatch, best)
        match.score = max(0, match.score - 10)
        db.session.commit()
        assert targets.list_targets(org.id, per_page=5).items[0].id == best


def test_score_refresh_is_an_hourly_job_of_its_own(app):
    import inspect

    from app import scheduler

    class Recorder:
        def __init__(self):
            self.ids = []

        def add_job(self, _func, _trigger, id, **_kwargs):  # noqa: A002
            self.ids.append(id)

    recorder = Recorder()
    scheduler.register_scheduler_jobs(recorder, app)
    assert "target_scores" in recorder.ids and "listing_matching" in recorder.ids
    source = inspect.getsource(scheduler.register_scheduler_jobs)
    matching = source[source.index("def run_listing_matching") : source.index("def run_target_scores")]
    assert "refresh_scores" not in matching
