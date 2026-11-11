from __future__ import annotations

from datetime import datetime, timezone

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
    remonttityyppi: Mapped[str] = mapped_column(String(100), nullable=False)
    # every toimiala the superadmin picked for this customer (remonttityyppi
    # above = the first one); None on invites created before this existed
    remonttityypit: Mapped[list | None] = mapped_column(json_type, nullable=True)
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
    def wanted_types(self) -> list[str]:
        types = [t for t in (self.remonttityypit or []) if t]
        return types or [self.remonttityyppi]

    def is_valid(self, *, now: datetime | None = None) -> bool:
        if self.accepted_at is not None or self.revoked_at is not None:
            return False
        now = now or datetime.now(timezone.utc)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return now < expires_at
