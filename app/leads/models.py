from datetime import datetime, timezone

from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

LEAD_STATUSES = ("active", "won", "lost", "archived")
LEAD_SOURCES = ("n8n", "manual", "import")
GDPR_CONSENT_SOURCES = ("api", "form", "manual")
GDPR_LEGAL_BASES = ("consent", "legitimate_interest", "contract")
AI_ENRICHMENT_STATUSES = ("pending", "processing", "completed", "failed", "disabled")
ACTIVITY_TYPES = (
    "note",
    "email_sent",
    "email_opened",
    "email_clicked",
    "call",
    "stage_changed",
    "ai_enriched",
    "created",
    "updated",
    "archived",
    "assigned",
    "task_created",
    "task_completed",
    "task_reminder_sent",
    "sequence_enrolled",
    "sequence_email_sent",
    "sequence_unenrolled",
    "meeting_scheduled",
    "meeting_cancelled",
    "proposal_sent",
    "proposal_viewed",
    "proposal_accepted",
    "proposal_declined",
)

DEFAULT_PIPELINE_STAGES = [
    ("New Lead", 0, "#3B82F6"),
    ("Contacted", 1, "#8B5CF6"),
    ("Interested", 2, "#F59E0B"),
    ("Proposal Sent", 3, "#06B6D4"),
    ("Won", 4, "#22C55E"),
    ("Lost", 5, "#EF4444"),
]


class PipelineStage(db.Model):
    __tablename__ = "pipeline_stages"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_pipeline_stage_org_name"),
        UniqueConstraint("organization_id", "order_index", name="uq_pipeline_stage_org_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    leads: Mapped[list["Lead"]] = relationship("Lead", back_populates="stage")


class Lead(db.Model):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("organization_id", "source", "source_ref", name="uq_lead_org_source_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    assigned_to: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )

    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(150), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    stage_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pipeline_stages.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    ai_enriched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_company_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ai_contact_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ai_enrichment_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="disabled"
    )
    ai_enrichment_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    close_probability: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    probability_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expected_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    deal_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unsubscribed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    gdpr_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gdpr_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gdpr_consent_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gdpr_legal_basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_anonymized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped["Organization"] = relationship("Organization")  # noqa: F821
    stage: Mapped[PipelineStage] = relationship("PipelineStage", back_populates="leads")
    assignee: Mapped["User | None"] = relationship("User", foreign_keys=[assigned_to])  # noqa: F821
    activities: Mapped[list["Activity"]] = relationship(
        "Activity", back_populates="lead", order_by="Activity.created_at.desc()"
    )

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return " ".join(parts)
        if self.company:
            return self.company
        if self.email:
            return self.email
        if self.phone:
            return self.phone
        return f"Lead #{self.id}"


class Activity(db.Model):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    lead: Mapped[Lead] = relationship("Lead", back_populates="activities")
    user: Mapped["User | None"] = relationship("User", foreign_keys=[user_id])  # noqa: F821
