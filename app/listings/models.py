"""Shared Oikotie listing catalogue.

One row per Oikotie listing, shared by every organization. n8n crawls Oikotie
continuously into this catalogue (Crawler), analyses each listing once with AI
into renovation types (Analyzer) and looks up isännöitsijä contacts only for
listings some active profile actually wants (Contact Enricher). Search
profiles are filters over the catalogue: a match is recorded in
`listing_matches` and turned into a lead in that organization's CRM.
See docs/ARKKITEHTUURI_KOHDEKATALOGI.md.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    JSON,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

ANALYSIS_STATUSES = ("pending", "processing", "done", "failed")
# none = contacts not needed (yet) / already known from the listing itself.
CONTACT_STATUSES = ("none", "pending", "processing", "done", "not_found")
# pending = available on the customer's Kohteet page; dismissed = hidden there
# by the customer; lead_created / merged = moved to a lead.
MATCH_STATUSES = ("pending", "dismissed", "lead_created", "merged")
CRAWL_MODES = ("incremental", "full")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Listing(db.Model):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_listing_source_external_id"),
        Index("ix_listings_analysis_queue", "analysis_status", "is_active"),
        Index("ix_listings_contact_queue", "contact_status", "is_active"),
        Index("ix_listings_maakunta_active", "maakunta", "is_active"),
        Index("ix_listings_company_key", "company_key"),
        Index("ix_listings_company_name_key", "company_name_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), default="oikotie", nullable=False)
    external_id: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    maakunta: Mapped[str] = mapped_column(String(50), nullable=False)
    kaupunki: Mapped[str | None] = mapped_column(String(100), nullable=True)
    osoite: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hinta: Mapped[str | None] = mapped_column(String(50), nullable=True)
    alue: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # From the Oikotie card when present, otherwise from the AI analysis.
    build_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    building_type: Mapped[str | None] = mapped_column(String(30), nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # AI analysis (one call per listing, all renovation types at once)
    analysis_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    analysis_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extracted from the listing page by the analyzer, then completed by the
    # contact enricher (Finder / Asiakastieto) when a profile needs it.
    taloyhtio: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tulevat_remontit: Mapped[str | None] = mapped_column(Text, nullable=True)
    isannoitsija: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # contact person at the property management office (isännöitsijä by name)
    isannoitsija_henkilo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    isannoitsija_puh: Mapped[str | None] = mapped_column(String(50), nullable=True)
    isannoitsija_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    isannoitsija_website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contact_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # the housing company's business ID (Y-tunnus), when the source gives it
    ytunnus: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # other people to reach at the housing company: the chair of the board
    # (hallituksen puheenjohtaja; a natural person, processed on legitimate
    # interest like the manager) and the maintenance company (huoltoyhtiö)
    hallitus_pj: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hallitus_pj_puh: Mapped[str | None] = mapped_column(String(50), nullable=True)
    hallitus_pj_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    huoltoyhtio: Mapped[str | None] = mapped_column(String(255), nullable=True)
    huoltoyhtio_puh: Mapped[str | None] = mapped_column(String(50), nullable=True)
    huoltoyhtio_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # map position (app/listings/geo.py). geo_precision: "source" (given by the
    # import) or "address" (geocoded); NULL = not known, the map then uses the
    # town's centre. geocoded_at = last geocoding attempt, also a failed one.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    geo_precision: Mapped[str | None] = mapped_column(String(10), nullable=True)
    geocoded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # One housing company across listings and sources (app/listings/housing.py):
    # "y:<Y-tunnus>" or "n:<name>|<city>"; NULL = the listing stands alone.
    company_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    company_name_key: Mapped[str | None] = mapped_column(String(300), nullable=True)

    contact_status: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    contact_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contact_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    renovations: Mapped[list["ListingRenovation"]] = relationship(
        "ListingRenovation",
        back_populates="listing",
        cascade="all, delete-orphan",
    )

    @property
    def source_ref(self) -> str:
        return f"{self.source}-{self.external_id}"

    @property
    def has_contact(self) -> bool:
        return bool((self.isannoitsija_puh or "").strip() or (self.isannoitsija_email or "").strip())


class ListingRenovation(db.Model):
    """An upcoming (not already done) renovation the AI found in a listing,
    classified into exactly one of app.search.constants.REMONTTITYYPIT."""

    __tablename__ = "listing_renovations"
    __table_args__ = (Index("ix_listing_renovations_type_conf", "remonttityyppi", "confidence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    remonttityyppi: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    timing: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # first year mentioned in `timing` (e.g. "PTS 2027-2029" -> 2027), if any
    timing_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    listing: Mapped[Listing] = relationship("Listing", back_populates="renovations")


class ListingMatch(db.Model):
    """A listing that satisfies a search profile. Created as "pending": it is
    shown on the organization's Kohteet page until a user moves it to a lead
    (app/listings/target_services.py) or hides it. Status stays
    "lead_created" even if the lead is later deleted, so a deleted lead is
    never silently recreated."""

    __tablename__ = "listing_matches"
    __table_args__ = (
        UniqueConstraint("profile_id", "listing_id", name="uq_listing_match_profile_listing"),
        Index("ix_listing_matches_status", "status", "profile_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    # NULL once the profile is deleted: handled matches outlive their profile.
    profile_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("search_profiles.id", ondelete="CASCADE"), nullable=True, index=True
    )
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    lead_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When the match became visible on Kohteet. NULL = still in the reserve
    # (TARGET_FETCH_ENABLED); a time in the near future = released by "Hae
    # kohteita" and appearing when its progress animation ends.
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 0-100, app/listings/housing.py::score_target over the housing company's
    # listings; refreshed by the scheduler (timing and freshness drift)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    listing: Mapped[Listing] = relationship("Listing")
    profile: Mapped["SearchProfile | None"] = relationship("SearchProfile")  # noqa: F821


class SavedTargetSearch(db.Model):
    """A filter combination on the Kohteet page that a user saved under a
    name: one click applies it, and (optionally) the user is notified when new
    targets match it."""

    __tablename__ = "saved_target_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # query-string parameters of /targets: search, profile_id and the filter keys
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    notify: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class CrawlState(db.Model):
    """Per-maakunta crawl cursor. Incremental crawls always start from the
    newest listings; the full pass (backfill + weekly refresh, which also
    detects removed listings) advances `full_offset` a few pages per run so no
    single n8n execution has to walk a whole region."""

    __tablename__ = "listing_crawl_state"
    __table_args__ = (UniqueConstraint("source", "maakunta", name="uq_crawl_state_source_maakunta"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), default="oikotie", nullable=False)
    maakunta: Mapped[str] = mapped_column(String(50), nullable=False)
    full_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    full_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # start of the previous full pass; listings unseen since then were missed
    # by two passes in a row and are marked removed
    prev_full_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    full_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_incremental_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PropertyManagerOffice(db.Model):
    """An isännöintitoimisto (property management office), shared by every
    organization like the catalogue. Listings name the office in
    `Listing.isannoitsija`; this row adds what a salesperson needs to work the
    office as a whole: business ID, address, switchboard, email and website.
    Filled from import files (listing rows or an office list) and the API."""

    __tablename__ = "property_manager_offices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # app/listings/contacts.py::office_key(name): lowercase, no legal form
    name_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ytunnus: Mapped[str | None] = mapped_column(String(20), nullable=True)
    osoite: Mapped[str | None] = mapped_column(String(255), nullable=True)
    postinumero: Mapped[str | None] = mapped_column(String(10), nullable=True)
    kaupunki: Mapped[str | None] = mapped_column(String(100), nullable=True)
    puhelin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
