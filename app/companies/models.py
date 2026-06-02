from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, and_, func, Table, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


class Company(db.Model):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), default="Finland")
    revenue_range: Mapped[str | None] = mapped_column(String(50), nullable=True)
    employee_count: Mapped[str | None] = mapped_column(String(50), nullable=True)
    type: Mapped[str | None] = mapped_column(String(50), default="prospect")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        nullable=False,
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    organization = relationship("Organization", backref="companies")  # noqa: F821
    contacts = relationship("Contact", back_populates="company", lazy="dynamic")
    leads = relationship(
        "Lead",
        back_populates="company_rel",
        lazy="dynamic",
        foreign_keys="Lead.company_id",
    )


lead_contacts = Table(
    "lead_contacts",
    db.metadata,
    Column("lead_id", Integer, ForeignKey("leads.id"), primary_key=True),
    Column("contact_id", Integer, ForeignKey("contacts.id"), primary_key=True),
)


class Contact(db.Model):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    company_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("companies.id"), nullable=True, index=True
    )

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        nullable=False,
    )

    organization = relationship("Organization", backref="contacts")  # noqa: F821
    company = relationship("Company", back_populates="contacts")
    leads = relationship(
        "Lead",
        secondary=lead_contacts,
        back_populates="contacts",
        primaryjoin=lambda: db.metadata.tables["contacts"].c.id
        == lead_contacts.c.contact_id,
        secondaryjoin=lambda: and_(
            db.metadata.tables["leads"].c.id == lead_contacts.c.lead_id,
            db.metadata.tables["leads"].c.organization_id
            == db.metadata.tables["contacts"].c.organization_id,
        ),
    )

