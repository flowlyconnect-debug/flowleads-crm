"""Daily and weekly SearchJob creation for active profiles."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.search.models import SearchJob, SearchProfile

STUCK_JOB_MAX_RETRIES = 2
STUCK_JOB_STALE_MINUTES = 30
STUCK_JOB_ERROR = "Stuck in running state — reset by the stuck-job reaper"


def create_daily_search_jobs(*, now: datetime | None = None) -> int:
    """Create one pending job per active daily profile if none exists for today."""
    when = now or datetime.now(timezone.utc)
    today = when.date()
    active_profiles = SearchProfile.query.filter_by(
        is_active=True,
        schedule_description="daily",
    ).all()
    created = 0
    for profile in active_profiles:
        existing = SearchJob.query.filter(
            SearchJob.search_profile_id == profile.id,
            SearchJob.scheduled_at >= today,
            SearchJob.status.in_(["pending", "running"]),
        ).first()
        if existing:
            continue
        db.session.add(
            SearchJob(
                search_profile_id=profile.id,
                organization_id=profile.organization_id,
                status="pending",
                scheduled_at=when,
            )
        )
        created += 1
    if created:
        db.session.commit()
    return created


def create_weekly_search_jobs(*, now: datetime | None = None) -> int:
    """Create one pending job per active weekly profile if none exists this ISO week."""
    when = now or datetime.now(timezone.utc)
    week_start = when.date() - timedelta(days=when.weekday())
    active_profiles = SearchProfile.query.filter_by(
        is_active=True,
        schedule_description="weekly",
    ).all()
    created = 0
    for profile in active_profiles:
        existing = SearchJob.query.filter(
            SearchJob.search_profile_id == profile.id,
            SearchJob.scheduled_at >= week_start,
            SearchJob.status.in_(["pending", "running"]),
        ).first()
        if existing:
            continue
        scheduled_at = when + timedelta(minutes=(profile.id % 60))
        db.session.add(
            SearchJob(
                search_profile_id=profile.id,
                organization_id=profile.organization_id,
                status="pending",
                scheduled_at=scheduled_at,
            )
        )
        created += 1
    if created:
        db.session.commit()
    return created


def create_missing_test_jobs(*, now: datetime | None = None) -> dict[str, int]:
    """Create pending jobs for active profiles without a pending/running job (dev use)."""
    when = now or datetime.now(timezone.utc)
    active_profiles = SearchProfile.query.filter_by(is_active=True).all()
    created = 0
    skipped = 0
    for profile in active_profiles:
        existing = SearchJob.query.filter(
            SearchJob.search_profile_id == profile.id,
            SearchJob.status.in_(["pending", "running"]),
        ).first()
        if existing:
            skipped += 1
            continue
        db.session.add(
            SearchJob(
                search_profile_id=profile.id,
                organization_id=profile.organization_id,
                status="pending",
                scheduled_at=when,
            )
        )
        created += 1
    if created:
        db.session.commit()
    return {"created": created, "skipped": skipped}


_RESET_RUNNING_ERROR = "Reset from running during n8n dev testing"


def reset_running_test_jobs() -> dict[str, int]:
    """Mark stuck running jobs as failed so dev test jobs can be recreated."""
    running_jobs = SearchJob.query.filter_by(status="running").all()
    reset = 0
    for job in running_jobs:
        job.status = "failed"
        job.error_message = _RESET_RUNNING_ERROR
        reset += 1
    if reset:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    return {"reset": reset}


def reap_stuck_running_jobs(
    *,
    now: datetime | None = None,
    stale_after_minutes: int = STUCK_JOB_STALE_MINUTES,
    max_retries: int = STUCK_JOB_MAX_RETRIES,
) -> dict[str, int]:
    """Production watchdog for jobs n8n never finished (crash, dropped execution).

    A job stuck "running" past `stale_after_minutes` is either requeued
    ("pending", so the next claim picks it up again) if it has retries left,
    or marked "failed" once `max_retries` is exceeded — using the retry_count
    column that previously existed but was never incremented anywhere.
    """
    when = now or datetime.now(timezone.utc)
    cutoff = when - timedelta(minutes=stale_after_minutes)
    stuck_jobs = SearchJob.query.filter(
        SearchJob.status == "running",
        SearchJob.started_at.isnot(None),
        SearchJob.started_at < cutoff,
    ).all()

    requeued = 0
    failed = 0
    for job in stuck_jobs:
        job.retry_count = (job.retry_count or 0) + 1
        if job.retry_count <= max_retries:
            job.status = "pending"
            job.started_at = None
            job.scheduled_at = when
            requeued += 1
        else:
            job.status = "failed"
            job.completed_at = when
            job.error_message = STUCK_JOB_ERROR
            failed += 1

    if stuck_jobs:
        db.session.commit()
    return {"requeued": requeued, "failed": failed}


def notify_failed_search_jobs() -> int:
    """Alert superadmins once per failed job (never re-sent for the same job).

    Previously a failed search job raised no alert at all — distinguishing
    "no new listings" (leads_found=0, status=completed) from "the search
    itself failed" (status=failed) only mattered on an admin going to look;
    nothing told them to look.
    """
    from app.email.services import EmailService
    from app.users.models import User

    failed_jobs = (
        SearchJob.query.filter(
            SearchJob.status == "failed",
            SearchJob.failure_notified_at.is_(None),
        )
        .order_by(SearchJob.id.asc())
        .all()
    )
    if not failed_jobs:
        return 0

    payload = [
        {
            "job_id": job.id,
            "organization_name": job.organization.name if job.organization else "",
            "profile_name": job.profile.name if job.profile else "",
            "error_message": job.error_message or "",
        }
        for job in failed_jobs
    ]

    superadmins = User.query.filter_by(role="superadmin", is_active=True).all()
    for admin in superadmins:
        EmailService.send_search_job_failed_alert(to_email=admin.email, failed_jobs=payload)

    now = datetime.now(timezone.utc)
    for job in failed_jobs:
        job.failure_notified_at = now
    db.session.commit()
    return len(failed_jobs)
