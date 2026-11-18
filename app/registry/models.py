"""Raw register data and the signals derived from it (internal, see __init__)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

SIGNAL_KINDS = ("permit", "heating", "age")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RegistryHousingCompany(db.Model):
    """One asunto-osakeyhtiö from the PRH open data (YTJ v3)."""

    __tablename__ = "registry_housing_companies"
    __table_args__ = (
        Index("ix_registry_companies_municipality", "municipality"),
        Index("ix_registry_companies_company_key", "company_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # same key as Listing.company_key (housing.name_key), for joining later
    company_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # the municipality the company was fetched for (PRH `location`)
    municipality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    street: Mapped[str | None] = mapped_column(String(255), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    post_office: Mapped[str | None] = mapped_column(String(100), nullable=True)
    registration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    prh_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    # the shared taloyhtiö (app/listings/housing_registry.py)
    housing_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("housing_companies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    buildings = relationship("RegistryBuilding", back_populates="housing_company")


class RegistryBuilding(db.Model):
    """One residential building (kerrostalo, or pientalo with 3+ apartments)
    from Ryhti `avoimet_rakennukset`."""

    __tablename__ = "registry_buildings"
    __table_args__ = (
        Index("ix_registry_buildings_municipality", "municipality"),
        Index("ix_registry_buildings_company", "housing_company_id"),
        Index("ix_registry_buildings_address_pending", "addresses_fetched_at"),
        Index("ix_registry_buildings_building_id", "building_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    building_key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    building_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    property_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    municipality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # "Kerrostalo" or "Rivitalo" (Ryhti "Pientalo" with 3+ apartments)
    building_type: Mapped[str] = mapped_column(String(30), nullable=False)
    completion_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    apartments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floor_area: Mapped[int | None] = mapped_column(Integer, nullable=True)
    facade_material: Mapped[str | None] = mapped_column(String(50), nullable=True)
    construction_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    heating_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    heating_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    lat: Mapped[float | None] = mapped_column(db.Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(db.Float, nullable=True)
    # Ryhti open_address: first address and every "street number" (folded)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    post_office: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address_keys: Mapped[list | None] = mapped_column(JSON, nullable=True)
    addresses_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    housing_company_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("registry_housing_companies.id", ondelete="SET NULL"), nullable=True
    )
    # "name" = street and number in the company name, "prh_address" = PRH address
    link_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # the shared taloyhtiö (app/listings/housing_registry.py): its company's, or
    # a nameless one known by this building's address
    housing_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("housing_companies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    ryhti_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    housing_company = relationship("RegistryHousingCompany", back_populates="buildings")


class RegistryPermit(db.Model):
    """An alteration permit for a residential building (Ryhti
    `avoimet_lupa_rakennukset`). The permit-stage building has its own
    building_key: it joins the completed building on the permanent building
    identifier (`building_id`), which may not be in registry_buildings yet."""

    __tablename__ = "registry_permits"
    __table_args__ = (
        UniqueConstraint("permit_id", "building_key", name="uq_registry_permit_building"),
        Index("ix_registry_permits_building_id", "building_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    permit_id: Mapped[str] = mapped_column(String(30), nullable=False)
    building_key: Mapped[str] = mapped_column(String(40), nullable=False)
    building_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    municipality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    building_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    action_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    decision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ryhti_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class RegistrySignal(db.Model):
    """A renovation the registers suggest for one building. `kind`: permit
    (an alteration permit), heating (oil heating) or age (typical interval
    from completion). Estimates, never facts."""

    __tablename__ = "registry_signals"
    __table_args__ = (
        UniqueConstraint("building_id", "kind", "renovation_type", name="uq_registry_signal"),
        Index("ix_registry_signals_kind", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    building_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registry_buildings.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    renovation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    due_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    building = relationship("RegistryBuilding")


class RegistrySyncState(db.Model):
    """Progress of one sync: `ryhti_buildings`, `ryhti_permits`,
    `ryhti_addresses` or `prh:<kunta>`."""

    __tablename__ = "registry_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    # newest source timestamp seen (Ryhti aikaleima_utc), for the next delta
    cursor: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
