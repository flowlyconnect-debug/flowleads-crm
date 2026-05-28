from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Index, Integer
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


class OrgLeadSettings(db.Model):
    __tablename__ = "org_lead_settings"
    __table_args__ = (
        Index("ix_org_lead_settings_organization_id", "organization_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, unique=True
    )

    default_pipeline_stage_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("pipeline_stages.id"), nullable=True
    )
    default_owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    default_tags: Mapped[list] = mapped_column(json_type, default=list, nullable=False)

    last_lead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_lead_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    organization = relationship(
        "Organization",
        backref=db.backref("lead_settings", uselist=False),
    )
    default_pipeline_stage = relationship("PipelineStage")
    default_owner = relationship("User", foreign_keys=[default_owner_id])
