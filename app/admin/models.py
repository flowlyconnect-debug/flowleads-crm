from __future__ import annotations

from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

INVITE_DEFAULT_TTL_DAYS = 7


class CustomerInvite(db.Model):
    """A one-time, expiring invite that lets a new customer admin finish their
    own onboarding: set a password and choose regions/features themselves.

    Only one *outstanding* (not accepted, not revoked, not expired) invite is
    meaningful per organization at a time — renewing an invite revokes the
    previous one so its link stops working.
    """

    __tablename__ = "customer_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    # Set by the inviting superadmin: what the invited admin is allowed to pick from.
    allowed_features: Mapped[list] = mapped_column(json_type, default=list, nullable=False)
    search_profile_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # one or more renovation types, "; "-separated (see invite_types())
    remonttityyppi: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="oikotie", nullable=False)

    invited_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship("Organization")  # noqa: F821

    @property
    def types(self) -> list[str]:
        return [t.strip() for t in (self.remonttityyppi or "").split(";") if t.strip()]

    def is_valid(self, *, now: datetime | None = None) -> bool:
        if self.accepted_at is not None or self.revoked_at is not None:
            return False
        now = now or datetime.now(timezone.utc)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return now < expires_at


class UsageDaily(db.Model):
    """How much a customer's user did of one thing on one day: page views of
    contact data (Kohteet, taloyhtiökortti, Isännöitsijät, kartta, liidi),
    transfers and rows taken out (CSV, CRM, API). Superadmin Asiakasseuranta
    (`app/admin/activity_services.py`) reads it to spot a customer who
    collects contacts to resell them. One row per (organization, user, day,
    metric), incremented by `app/core/usage.py::track`."""

    __tablename__ = "usage_daily"
    __table_args__ = (
        sa.UniqueConstraint("organization_id", "user_id", "day", "metric", name="uq_usage_daily"),
        sa.Index("ix_usage_daily_org_day", "organization_id", "day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    day: Mapped[date] = mapped_column(sa.Date, nullable=False)
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class CustomerAnnouncement(db.Model):
    """An e-mail a superadmin sent to customers from Asiakastiedotteet
    (`app/admin/announcement_services.py`), e.g. about an update."""

    __tablename__ = "customer_announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # "admins" = the customers' admins, "all" = every user of the customers
    audience: Mapped[str] = mapped_column(String(20), nullable=False, default="admins")
    # chosen customers; empty = every active customer
    organization_ids: Mapped[list | None] = mapped_column(json_type, nullable=True)
    sent_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failures: Mapped[list | None] = mapped_column(json_type, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    sent_by: Mapped["User | None"] = relationship("User")  # noqa: F821
