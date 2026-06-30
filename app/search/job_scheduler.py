"""Daily SearchJob creation for active profiles."""

from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.search.models import SearchJob, SearchProfile


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
