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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

TASK_TYPES = ("call", "email", "follow_up", "meeting", "other")
TASK_STATUSES = ("pending", "in_progress", "completed", "cancelled")
TASK_PRIORITIES = ("low", "normal", "high", "urgent")

AUTO_TASK_TRIGGERS = ("new_lead", "no_contact", "stage_change")


class OrganizationSettings(db.Model):
    """Per-organization task automation and related settings."""

    __tablename__ = "organization_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, unique=True, index=True
    )
    auto_task_on_new_lead: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_task_no_contact_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    auto_task_stage_change: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    gdpr_default_legal_basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gdpr_retention_days: Mapped[int] = mapped_column(Integer, default=730, nullable=False)
    gdpr_auto_anonymize_inactive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    privacy_policy_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    data_controller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_controller_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    proposal_sequence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proposal_move_lead_to_won_on_accept: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    proposal_default_valid_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    proposal_default_tax_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("24")
    )
    proposal_default_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    default_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")

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


class Task(db.Model):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("leads.id"), nullable=True, index=True
    )
    assigned_to: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="follow_up")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    priority: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")

    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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
    lead: Mapped["Lead | None"] = relationship("Lead", foreign_keys=[lead_id])  # noqa: F821
    assignee: Mapped["User"] = relationship("User", foreign_keys=[assigned_to])  # noqa: F821
    creator: Mapped["User | None"] = relationship("User", foreign_keys=[created_by])  # noqa: F821

    @property
    def is_overdue(self) -> bool:
        if self.status in ("completed", "cancelled"):
            return False
        now = datetime.now(timezone.utc)
        due = self.due_date
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return due < now
