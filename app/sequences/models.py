from datetime import datetime, timezone

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.extensions import db

TRIGGER_TYPES = (
    "manual",
    "on_lead_created",
    "on_stage_change",
    "on_segment_match",
)

ENROLLMENT_STATUSES = ("active", "completed", "cancelled", "unsubscribed")

json_type = JSON().with_variant(JSONB, "postgresql")


class EmailSequence(db.Model):
    __tablename__ = "email_sequences"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_email_sequence_org_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    trigger_config: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
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
    creator: Mapped["User | None"] = relationship("User", foreign_keys=[created_by])  # noqa: F821
    steps: Mapped[list["EmailSequenceStep"]] = relationship(
        "EmailSequenceStep",
        back_populates="sequence",
        order_by="EmailSequenceStep.order_index",
        cascade="all, delete-orphan",
    )
    enrollments: Mapped[list["EmailSequenceEnrollment"]] = relationship(
        "EmailSequenceEnrollment", back_populates="sequence"
    )


class EmailSequenceStep(db.Model):
    __tablename__ = "email_sequence_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequence_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_sequences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delay_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delay_hours: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    subject_template: Mapped[str] = mapped_column(String(500), nullable=False)
    body_html_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[dict | None] = mapped_column(json_type, nullable=True)
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

    sequence: Mapped[EmailSequence] = relationship("EmailSequence", back_populates="steps")


class EmailSequenceEnrollment(db.Model):
    __tablename__ = "email_sequence_enrollments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequence_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_sequences.id"), nullable=False, index=True
    )
    lead_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leads.id"), nullable=False, index=True
    )
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    enrolled_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    current_step_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_send_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    sequence: Mapped[EmailSequence] = relationship("EmailSequence", back_populates="enrollments")
    lead: Mapped["Lead"] = relationship("Lead")  # noqa: F821
    sent_messages: Mapped[list["EmailSequenceSent"]] = relationship(
        "EmailSequenceSent", back_populates="enrollment"
    )


class EmailSequenceSent(db.Model):
    __tablename__ = "email_sequence_sents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_sequence_enrollments.id"), nullable=False, index=True
    )
    step_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("email_sequence_steps.id"), nullable=False, index=True
    )
    lead_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leads.id"), nullable=False, index=True
    )
    email_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("email_logs.id"), nullable=True
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    enrollment: Mapped[EmailSequenceEnrollment] = relationship(
        "EmailSequenceEnrollment", back_populates="sent_messages"
    )
    step: Mapped[EmailSequenceStep] = relationship("EmailSequenceStep")
    email_log: Mapped["EmailLog | None"] = relationship("EmailLog")  # noqa: F821
