"""Connections to external CRMs and what each lead became there."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

PROVIDERS = ("hubspot", "pipedrive")
PROVIDER_LABELS = {"hubspot": "HubSpot", "pipedrive": "Pipedrive"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CrmConnection(db.Model):
    """An organization's API token for one external CRM. The token is
    encrypted with WEBHOOK_ENCRYPTION_KEY (app/webhooks/encryption.py)."""

    __tablename__ = "crm_connections"
    __table_args__ = (UniqueConstraint("organization_id", "provider", name="uq_crm_connection_org_provider"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # HubSpot: deal pipeline and stage ids; Pipedrive: nothing yet
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    account_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connected_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CrmExportLink(db.Model):
    """The company, contact and deal a lead became in an external CRM: a
    second export updates them instead of creating duplicates."""

    __tablename__ = "crm_export_links"
    __table_args__ = (UniqueConstraint("lead_id", "provider", name="uq_crm_export_link_lead_provider"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id: Mapped[int] = mapped_column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    company_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
