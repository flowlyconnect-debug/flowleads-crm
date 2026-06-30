from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.search.job_scheduler import create_daily_search_jobs
from app.search.models import SearchJob, SearchProfile
from app.users.services import create_organization


def _create_daily_profile(app, *, is_active=True, schedule="daily", slug_suffix=""):
    with app.app_context():
        suffix = slug_suffix or str(datetime.now(timezone.utc).timestamp()).replace(".", "")
        org = create_organization("Scheduler Org", f"scheduler-org-{suffix}")
        db.session.flush()
        profile = SearchProfile(
            organization_id=org.id,
            name="Daily profile",
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
