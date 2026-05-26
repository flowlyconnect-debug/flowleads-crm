from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
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

PROPOSAL_STATUSES = (
    "draft",
    "sent",
    "viewed",
    "accepted",
    "declined",
    "expired",
)


class Proposal(db.Model):
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    lead_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leads.id"), nullable=False, index=True
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)

    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")

    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    discount_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    tax_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    view_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)

    signature_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature_user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    opened_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead_name_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lead_company_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lead_email_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

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

    line_items: Mapped[list["ProposalLineItem"]] = relationship(
        "ProposalLineItem",
        back_populates="proposal",
        order_by="ProposalLineItem.order_index",
        cascade="all, delete-orphan",
    )
    lead: Mapped["Lead"] = relationship("Lead", foreign_keys=[lead_id])  # noqa: F821
    creator: Mapped["User | None"] = relationship("User", foreign_keys=[created_by])  # noqa: F821

    @property
    def is_editable(self) -> bool:
        return self.status == "draft" and not self.is_locked

    @property
    def is_expired(self) -> bool:
        if self.status in ("accepted", "declined", "expired"):
            return self.status == "expired"
        if self.valid_until is None:
            return False
        return self.valid_until < date.today()


class ProposalLineItem(db.Model):
    __tablename__ = "proposal_line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("1"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    discount_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))

    proposal: Mapped[Proposal] = relationship("Proposal", back_populates="line_items")


class ProposalTemplate(db.Model):
    __tablename__ = "proposal_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    default_valid_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    default_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_tax_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("24")
    )
    header_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    footer_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship("Organization")  # noqa: F821
