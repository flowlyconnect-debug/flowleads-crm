from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

SEARCH_JOB_STATUSES = ("pending", "running", "completed", "failed")
SEARCH_SCHEDULES = ("daily", "weekly", "manual")


class SearchProfile(db.Model):
    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    remonttityyppi: Mapped[str] = mapped_column(String(100), nullable=False)
    regions: Mapped[list] = mapped_column(json_type, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="oikotie", nullable=False)

    crm_api_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    schedule_description: Mapped[str] = mapped_column(
        String(50), default="daily", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_leads_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship("Organization")  # noqa: F821
    jobs: Mapped[list["SearchJob"]] = relationship(
        "SearchJob",
        back_populates="profile",
        lazy="dynamic",
    )


class SearchJob(db.Model):
    __tablename__ = "search_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("search_profiles.id"), nullable=False, index=True
    )
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    leads_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    leads_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Short-lived, job-scoped credential minted when this job is claimed (see
    # claim_pending_jobs_for_n8n). n8n uses it instead of the organization's
    # permanent crm_api_key, so a leaked execution log only exposes a token
    # scoped to this one job, not a standing key for the whole organization.
    n8n_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    n8n_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Progress reporting from the n8n dispatcher. A full-region run walks every
    # Oikotie result page one at a time and can take hours, so the dispatcher
    # PATCHes a heartbeat after each page. The stuck-job reaper keys off
    # heartbeat_at (not started_at) so a long but healthy run is never requeued.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pages_done: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    pages_failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    progress: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    profile: Mapped[SearchProfile] = relationship("SearchProfile", back_populates="jobs")
    organization: Mapped["Organization"] = relationship("Organization")  # noqa: F821


class SearchDedupe(db.Model):
    __tablename__ = "search_dedupe"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_id",
            name="uq_search_dedupe_org_source_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship("Organization")  # noqa: F821
