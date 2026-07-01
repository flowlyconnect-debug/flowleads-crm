from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.search.job_scheduler import create_daily_search_jobs, create_weekly_search_jobs
from app.search.models import SearchJob, SearchProfile
from app.users.services import create_organization


def _create_profile(app, *, is_active=True, schedule="daily", slug_suffix="", name=None):
    with app.app_context():
        suffix = slug_suffix or str(datetime.now(timezone.utc).timestamp()).replace(".", "")
        org = create_organization("Scheduler Org", f"scheduler-org-{suffix}")
        db.session.flush()
        profile = SearchProfile(
            organization_id=org.id,
            name=name or f"{schedule.title()} profile",
            remonttityyppi="Putkiremontti",
            regions=["Uusimaa"],
            source="oikotie",
            crm_api_key="fl_live_scheduler",
            schedule_description=schedule,
            is_active=is_active,
        )
        db.session.add(profile)
        db.session.commit()
        return {"org_id": org.id, "profile_id": profile.id}


def _create_daily_profile(app, *, is_active=True, schedule="daily", slug_suffix=""):
    return _create_profile(
        app, is_active=is_active, schedule=schedule, slug_suffix=slug_suffix
    )


def _create_weekly_profile(app, *, is_active=True, slug_suffix=""):
    return _create_profile(app, is_active=is_active, schedule="weekly", slug_suffix=slug_suffix)


def _as_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def test_active_daily_profile_creates_pending_search_job(app):
    ctx = _create_daily_profile(app)
    now = datetime.now(timezone.utc)

    with app.app_context():
        created = create_daily_search_jobs(now=now)
        assert created == 1

        job = SearchJob.query.filter_by(search_profile_id=ctx["profile_id"]).one()
        assert job.status == "pending"
        assert job.organization_id == ctx["org_id"]
        assert job.scheduled_at.date() == now.date()


def test_daily_scheduler_skips_when_pending_job_exists_today(app):
    ctx = _create_daily_profile(app)
    now = datetime.now(timezone.utc)

    with app.app_context():
        db.session.add(
            SearchJob(
                search_profile_id=ctx["profile_id"],
                organization_id=ctx["org_id"],
                status="pending",
                scheduled_at=now,
            )
        )
        db.session.commit()

        created = create_daily_search_jobs(now=now)
        assert created == 0
        assert SearchJob.query.filter_by(search_profile_id=ctx["profile_id"]).count() == 1


def test_daily_scheduler_skips_inactive_and_non_daily_profiles(app):
    _create_daily_profile(app, is_active=False, slug_suffix="inactive")
    _create_daily_profile(app, schedule="manual", slug_suffix="manual")
    now = datetime.now(timezone.utc)

    with app.app_context():
        assert create_daily_search_jobs(now=now) == 0


def test_daily_scheduler_creates_job_after_yesterdays_job_completed(app):
    ctx = _create_daily_profile(app)
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    with app.app_context():
        db.session.add(
            SearchJob(
                search_profile_id=ctx["profile_id"],
                organization_id=ctx["org_id"],
                status="completed",
                scheduled_at=yesterday,
            )
        )
        db.session.commit()

        created = create_daily_search_jobs(now=now)
        assert created == 1


def test_weekly_profile_creates_one_job_per_iso_week(app):
    ctx = _create_weekly_profile(app)
    monday = datetime(2026, 6, 29, 6, 0, tzinfo=timezone.utc)
    tuesday = monday + timedelta(days=1, hours=3)

    with app.app_context():
        assert create_weekly_search_jobs(now=monday) == 1
        assert create_weekly_search_jobs(now=tuesday) == 0

        jobs = SearchJob.query.filter_by(search_profile_id=ctx["profile_id"]).all()
        assert len(jobs) == 1
        assert jobs[0].status == "pending"
        assert jobs[0].organization_id == ctx["org_id"]


def test_weekly_profile_creates_new_job_on_next_iso_week(app):
    ctx = _create_weekly_profile(app)
    week_1_monday = datetime(2026, 6, 29, 6, 0, tzinfo=timezone.utc)
    week_2_monday = week_1_monday + timedelta(days=7)

    with app.app_context():
        assert create_weekly_search_jobs(now=week_1_monday) == 1
        assert create_weekly_search_jobs(now=week_2_monday) == 1

        jobs = (
            SearchJob.query.filter_by(search_profile_id=ctx["profile_id"])
            .order_by(SearchJob.scheduled_at)
            .all()
        )
        assert len(jobs) == 2
        assert _as_utc_naive(jobs[0].scheduled_at) >= _as_utc_naive(week_1_monday)
        assert _as_utc_naive(jobs[0].scheduled_at) < _as_utc_naive(week_2_monday)
        assert _as_utc_naive(jobs[1].scheduled_at) >= _as_utc_naive(week_2_monday)


def test_weekly_scheduler_skips_daily_profile(app):
    _create_daily_profile(app, slug_suffix="daily-only")
    monday = datetime(2026, 6, 29, 6, 0, tzinfo=timezone.utc)

    with app.app_context():
        assert create_weekly_search_jobs(now=monday) == 0


def test_weekly_scheduler_skips_inactive_profile(app):
    _create_weekly_profile(app, is_active=False, slug_suffix="inactive-weekly")
    monday = datetime(2026, 6, 29, 6, 0, tzinfo=timezone.utc)

    with app.app_context():
        assert create_weekly_search_jobs(now=monday) == 0


def test_weekly_scheduler_spreads_scheduled_at_by_profile_id(app):
    ctx_a = _create_weekly_profile(app, slug_suffix="weekly-a")
    ctx_b = _create_weekly_profile(app, slug_suffix="weekly-b")
    now = datetime(2026, 6, 29, 6, 0, tzinfo=timezone.utc)

    with app.app_context():
        assert create_weekly_search_jobs(now=now) == 2

        job_a = SearchJob.query.filter_by(search_profile_id=ctx_a["profile_id"]).one()
        job_b = SearchJob.query.filter_by(search_profile_id=ctx_b["profile_id"]).one()

        expected_a = now + timedelta(minutes=(ctx_a["profile_id"] % 60))
        expected_b = now + timedelta(minutes=(ctx_b["profile_id"] % 60))

        assert _as_utc_naive(job_a.scheduled_at) == _as_utc_naive(expected_a)
        assert _as_utc_naive(job_b.scheduled_at) == _as_utc_naive(expected_b)
        assert _as_utc_naive(job_a.scheduled_at) >= _as_utc_naive(now)
        assert _as_utc_naive(job_b.scheduled_at) >= _as_utc_naive(now)

        if ctx_a["profile_id"] % 60 != ctx_b["profile_id"] % 60:
            assert job_a.scheduled_at != job_b.scheduled_at
