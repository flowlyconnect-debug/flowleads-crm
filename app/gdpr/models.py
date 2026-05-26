from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

EXPORT_REQUEST_STATUSES = ("pending", "processing", "completed", "failed", "expired")
EXPORT_TYPES = ("lead", "organization")


class DataExportRequest(db.Model):
    __tablename__ = "data_export_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    requested_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    export_type: Mapped[str] = mapped_column(String(32), nullable=False)
    lead_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("leads.id"), nullable=True, index=True
    )
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    download_token: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped["Organization"] = relationship("Organization")  # noqa: F821
    requester: Mapped["User"] = relationship("User", foreign_keys=[requested_by])  # noqa: F821
    lead: Mapped["Lead | None"] = relationship("Lead", foreign_keys=[lead_id])  # noqa: F821
