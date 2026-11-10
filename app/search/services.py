from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, url_for
from sqlalchemy import update
from sqlalchemy.orm import joinedload

from app.core.security import hash_api_key
from app.extensions import db
from app.search.models import SEARCH_JOB_STATUSES, SearchDedupe, SearchJob, SearchProfile

# How long a job's n8n token stays valid after being claimed. Generous
# relative to a single search run, but short enough that a leaked execution
# log is only useful while this job is actually in flight.
N8N_JOB_TOKEN_TTL_MINUTES = 120


def crm_leads_endpoint() -> str:
    base = (current_app.config.get("APP_BASE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}/api/v1/leads"
    try:
        return url_for("api.create_lead", _external=True)
    except RuntimeError:
        return "/api/v1/leads"


def serialize_job_for_n8n(job: SearchJob, *, job_token: str | None = None) -> dict:
    """`crm_api_key` here is a short-lived job-scoped token (see
    claim_pending_jobs_for_n8n), never the organization's permanent API key —
    a leaked n8n execution log only exposes a credential good for this one
    job, not a standing key for the whole organization. Only the "pending"
    listing (which claims and mints the token) ever populates it; any other
    status listing is visibility-only and carries no usable credential."""
    profile = job.profile
    return {
        "job_id": job.id,
        "profile_id": job.search_profile_id,
        "organization_id": job.organization_id,
        "remonttityyppi": profile.remonttityyppi,
        "regions": list(profile.regions or []),
        "source": profile.source,
        "crm_api_key": job_token or "",
        "crm_endpoint": crm_leads_endpoint(),
    }


def list_jobs_for_n8n(*, status: str, limit: int) -> list[dict]:
    if status not in SEARCH_JOB_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    if status == "pending":
        claimed = claim_pending_jobs_for_n8n(limit=limit)
        return [serialize_job_for_n8n(job, job_token=token) for job, token in claimed]

    jobs = (
        SearchJob.query.options(joinedload(SearchJob.profile))
        .join(SearchProfile, SearchJob.search_profile_id == SearchProfile.id)
        .filter(SearchJob.status == status, SearchProfile.is_active.is_(True))
        .order_by(SearchJob.scheduled_at.asc(), SearchJob.id.asc())
        .limit(limit)
        .all()
    )
    return [serialize_job_for_n8n(job) for job in jobs]


def claim_pending_jobs_for_n8n(*, limit: int) -> list[tuple[SearchJob, str]]:
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

    claimed: list[tuple[SearchJob, str]] = []
    for job in candidates:
        if len(claimed) >= limit:
            break
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=N8N_JOB_TOKEN_TTL_MINUTES)
        result = db.session.execute(
            update(SearchJob)
            .where(SearchJob.id == job.id, SearchJob.status == "pending")
            .values(
                status="running",
                started_at=now,
                n8n_token_hash=hash_api_key(token),
                n8n_token_expires_at=expires_at,
            )
        )
        if result.rowcount == 1:
            job.status = "running"
            job.started_at = now
            job.n8n_token_expires_at = expires_at
            claimed.append((job, token))

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
    leads_found_delta: int | None = None,
    leads_sent_delta: int | None = None,
    page_done: bool = False,
    page_failed: bool = False,
    progress: str | None = None,
) -> SearchJob | None:
    """Apply an n8n status/progress update to a job.

    Two styles are supported:
    - absolute counters (`leads_found` / `leads_sent`), the original contract;
    - per-page heartbeats (`status="running"` + `*_delta`, `page_done`,
      `page_failed`, `progress`) sent by the dispatcher after every Oikotie
      page. Each heartbeat also slides the job token's expiry forward, so a
      multi-hour full-region run keeps a valid credential for as long as it
      keeps reporting in, without making the token long-lived up front.
    """
    if status not in ("running", "completed", "failed"):
        raise ValueError(f"Invalid status: {status}")
    for name, value in (("leads_found_delta", leads_found_delta), ("leads_sent_delta", leads_sent_delta)):
        if value is not None and value < 0:
            raise ValueError(f"{name} must be >= 0")

    job = (
        SearchJob.query.options(joinedload(SearchJob.profile))
        .filter_by(id=job_id)
        .first()
    )
    if job is None:
        return None

    # A late heartbeat (e.g. the last page finishing after the poller already
    # closed the job) must never reopen a finished job.
    if status == "running" and job.status in ("completed", "failed"):
        return job

    now = datetime.now(timezone.utc)
    job.status = status

    if leads_found is not None:
        job.leads_found = leads_found
    if leads_sent is not None:
        job.leads_sent = leads_sent
    if leads_found_delta:
        job.leads_found = (job.leads_found or 0) + leads_found_delta
    if leads_sent_delta:
        job.leads_sent = (job.leads_sent or 0) + leads_sent_delta
    if page_done or page_failed:
        job.pages_done = (job.pages_done or 0) + 1
    if page_failed:
        job.pages_failed = (job.pages_failed or 0) + 1
    if progress is not None:
        job.progress = progress[:255] or None
    if error is not None:
        job.error_message = error or None

    if status == "running":
        if job.started_at is None:
            job.started_at = now
        job.heartbeat_at = now
        if job.n8n_token_hash:
            job.n8n_token_expires_at = now + timedelta(minutes=N8N_JOB_TOKEN_TTL_MINUTES)
    elif status in ("completed", "failed"):
        job.completed_at = now
        # Invalidate the job's n8n token immediately rather than waiting out
        # its TTL — once a job is done, nothing should authenticate with it.
        job.n8n_token_hash = None
        job.n8n_token_expires_at = None
        profile = job.profile
        profile.last_run_at = now
        profile.total_runs = (profile.total_runs or 0) + 1
        if status == "completed":
            # Counters may have been accumulated by heartbeats / job-token lead
            # posts rather than sent in this final PATCH.
            sent = leads_sent if leads_sent is not None else (job.leads_sent or 0)
            profile.total_leads_sent = (profile.total_leads_sent or 0) + sent

    return job


def record_job_token_lead(job_id: int | None, *, created: int) -> None:
    """Count leads created through a job token straight onto the job, so the
    job's leads_sent is accurate even if n8n never reports a total."""
    if not job_id or created <= 0:
        return
    db.session.execute(
        update(SearchJob)
        .where(SearchJob.id == job_id)
        .values(leads_sent=SearchJob.leads_sent + created)
    )


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
