from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app, url_for
from sqlalchemy import update
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

    if status == "pending":
        jobs = claim_pending_jobs_for_n8n(limit=limit)
        return [serialize_job_for_n8n(job) for job in jobs]

    jobs = (
        SearchJob.query.options(joinedload(SearchJob.profile))
        .join(SearchProfile, SearchJob.search_profile_id == SearchProfile.id)
        .filter(SearchJob.status == status, SearchProfile.is_active.is_(True))
        .order_by(SearchJob.scheduled_at.asc(), SearchJob.id.asc())
        .limit(limit)
        .all()
    )
    return [serialize_job_for_n8n(job) for job in jobs]


def claim_pending_jobs_for_n8n(*, limit: int) -> list[SearchJob]:
    """Atomically hand out up to `limit` due pending jobs, marking each one
    "running" as part of the same call that returns it.

    Listing pending jobs used to be read-only: n8n would GET the list, then
    separately PATCH each job to "running". Two overlapping n8n runs (e.g. a
    scheduled trigger overlapping a manual one) could both GET the same
    pending job and both start working it. Folding the claim into this read
    closes that window: a conditional UPDATE (`WHERE status='pending'`) only
    ever succeeds for the execution that gets there first, on any backend —
    Postgres's row locking makes the second UPDATE affect zero rows once the
    first commits, which is exactly what `rowcount` checks below.
    """
    now = datetime.now(timezone.utc)
    candidates = (
        SearchJob.query.options(joinedload(SearchJob.profile))
        .join(SearchProfile, SearchJob.search_profile_id == SearchProfile.id)
        .filter(
            SearchJob.status == "pending",
            SearchProfile.is_active.is_(True),
            SearchJob.scheduled_at <= now,
        )
        .order_by(SearchJob.scheduled_at.asc(), SearchJob.id.asc())
        .limit(max(limit * 2, limit))
        .all()
    )

    claimed: list[SearchJob] = []
    for job in candidates:
        if len(claimed) >= limit:
            break
        result = db.session.execute(
            update(SearchJob)
            .where(SearchJob.id == job.id, SearchJob.status == "pending")
            .values(status="running", started_at=now)
        )
        if result.rowcount == 1:
            job.status = "running"
            job.started_at = now
            claimed.append(job)

    if claimed:
        db.session.commit()
    return claimed


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
