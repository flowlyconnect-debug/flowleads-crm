from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app, url_for
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.search.models import SEARCH_JOB_STATUSES, SearchDedupe, SearchJob, SearchProfile


def crm_leads_endpoint() -> str:
    base = (current_app.config.get("APP_BASE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}/api/v1/leads"
    try:
        return url_for("api.create_lead", _external=True)
    except RuntimeError:
        return "/api/v1/leads"


def serialize_job_for_n8n(job: SearchJob) -> dict:
    profile = job.profile
    return {
        "job_id": job.id,
        "profile_id": job.search_profile_id,
        "organization_id": job.organization_id,
        "remonttityyppi": profile.remonttityyppi,
        "regions": list(profile.regions or []),
        "source": profile.source,
        "crm_api_key": profile.crm_api_key or "",
        "crm_endpoint": crm_leads_endpoint(),
    }


def list_jobs_for_n8n(*, status: str, limit: int) -> list[dict]:
    if status not in SEARCH_JOB_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    now = datetime.now(timezone.utc)
    query = (
        SearchJob.query.options(joinedload(SearchJob.profile))
        .join(SearchProfile, SearchJob.search_profile_id == SearchProfile.id)
        .filter(
            SearchJob.status == status,
            SearchProfile.is_active.is_(True),
        )
    )
    if status == "pending":
        query = query.filter(SearchJob.scheduled_at <= now)

    jobs = (
        query.order_by(SearchJob.scheduled_at.asc(), SearchJob.id.asc())
        .limit(limit)
        .all()
    )
    return [serialize_job_for_n8n(job) for job in jobs]


def update_job_for_n8n(
    job_id: int,
    *,
    status: str,
    leads_found: int | None = None,
    leads_sent: int | None = None,
    error: str | None = None,
) -> SearchJob | None:
    if status not in ("running", "completed", "failed"):
        raise ValueError(f"Invalid status: {status}")

    job = (
        SearchJob.query.options(joinedload(SearchJob.profile))
        .filter_by(id=job_id)
        .first()
    )
    if job is None:
        return None

    now = datetime.now(timezone.utc)
    job.status = status

    if leads_found is not None:
        job.leads_found = leads_found
    if leads_sent is not None:
        job.leads_sent = leads_sent
    if error is not None:
        job.error_message = error or None

    if status == "running":
        if job.started_at is None:
            job.started_at = now
    elif status in ("completed", "failed"):
        job.completed_at = now
        profile = job.profile
        profile.last_run_at = now
        profile.total_runs = (profile.total_runs or 0) + 1
        if status == "completed" and leads_sent is not None:
            profile.total_leads_sent = (profile.total_leads_sent or 0) + leads_sent

    return job


def check_dedupe_for_n8n(organization_id: int, source_ids: list[str]) -> dict:
    if not source_ids:
        return {"new_ids": [], "existing_ids": []}

    existing_rows = SearchDedupe.query.filter(
        SearchDedupe.organization_id == organization_id,
        SearchDedupe.source_id.in_(source_ids),
    ).all()
    existing_set = {row.source_id for row in existing_rows}
    existing_ids = [sid for sid in source_ids if sid in existing_set]
    new_ids = [sid for sid in source_ids if sid not in existing_set]
    return {"new_ids": new_ids, "existing_ids": existing_ids}


def mark_dedupe_for_n8n(organization_id: int, source_ids: list[str]) -> int:
    if not source_ids:
        return 0

    existing_rows = SearchDedupe.query.filter(
        SearchDedupe.organization_id == organization_id,
        SearchDedupe.source_id.in_(source_ids),
    ).all()
    existing_set = {row.source_id for row in existing_rows}

    marked = 0
    for source_id in source_ids:
        if source_id in existing_set:
            continue
        db.session.add(
            SearchDedupe(organization_id=organization_id, source_id=source_id)
        )
        existing_set.add(source_id)
        marked += 1
    return marked
