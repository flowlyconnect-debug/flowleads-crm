from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

WEB_FORM_FIELD_TYPES = ("text", "email", "tel", "number", "textarea", "select", "checkbox")

WEB_FORM_SUBMISSION_STATUSES = ("processed", "duplicate", "spam", "failed")

LEAD_MAPPED_FIELD_KEYS = frozenset(
    {
        "first_name",
        "last_name",
        "email",
        "phone",
        "company",
        "title",
        "notes",
        "website",
        "linkedin_url",
    }
)


class WebForm(db.Model):
    __tablename__ = "web_forms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    form_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    submit_button_text: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Lähetä"
    )
    success_message: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="Kiitos! Otamme yhteyttä pian.",
    )
    fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    default_stage_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("pipeline_stages.id"), nullable=True
    )
    default_assigned_to: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    auto_enroll_sequence_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("email_sequences.id"), nullable=True
    )
    notify_users: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    submission_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
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
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization")  # noqa: F821
    creator = relationship("User", foreign_keys=[created_by])  # noqa: F821
    default_stage = relationship("PipelineStage")  # noqa: F821
    assignee = relationship("User", foreign_keys=[default_assigned_to])  # noqa: F821
    submissions = relationship("WebFormSubmission", back_populates="form")


class WebFormSubmission(db.Model):
    __tablename__ = "web_form_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    form_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("web_forms.id"), nullable=False, index=True
    )
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("leads.id"), nullable=True, index=True
    )
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processed", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    form = relationship("WebForm", back_populates="submissions")
    lead = relationship("Lead")  # noqa: F821
