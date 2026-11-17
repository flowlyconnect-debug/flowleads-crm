"""Running the register sync and the superadmin overview (internal, see __init__)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.registry.models import (
    RegistryBuilding,
    RegistryHousingCompany,
    RegistryPermit,
    RegistrySignal,
    RegistrySyncState,
)
from app.registry.signals import compute_signals
from app.registry.sync import fetch_addresses, link_companies, sync_buildings, sync_permits, sync_prh

logger = logging.getLogger(__name__)

STEPS = ("buildings", "permits", "addresses", "prh", "link", "signals")


def run_registry_sync(*, steps: tuple[str, ...] = STEPS, ryhti=None, prh=None, now: datetime | None = None) -> dict:
    """One pass of every step. Each step is bounded and resumable, so a run
    that stops part way loses nothing."""
    now = now or datetime.now(timezone.utc)
    result: dict = {}
    if "buildings" in steps:
        result["buildings"] = sync_buildings(ryhti, now=now)
    if "permits" in steps:
        result["permits"] = sync_permits(ryhti, now=now)
    if "addresses" in steps:
        result["addresses"] = fetch_addresses(ryhti, now=now)
    if "prh" in steps:
        result["prh"] = sync_prh(prh, now=now)
    if "link" in steps:
        result["linked"] = link_companies(now=now)
    if "signals" in steps:
        result["signals"] = compute_signals(now=now)
    logger.info("Registry sync: %s", result)
    return result


def registry_overview(*, municipality: str | None = None, kind: str | None = None, limit: int = 50) -> dict:
    """Counts, sync state and a sample of the strongest signals."""
    buildings = RegistryBuilding.query.count()
    linked = RegistryBuilding.query.filter(RegistryBuilding.housing_company_id.isnot(None)).count()
    with_address = RegistryBuilding.query.filter(RegistryBuilding.addresses_fetched_at.isnot(None)).count()
    by_kind = dict(
        db.session.query(RegistrySignal.kind, func.count(RegistrySignal.id)).group_by(RegistrySignal.kind).all()
    )
    by_type = (
        db.session.query(RegistrySignal.renovation_type, func.count(RegistrySignal.id))
        .group_by(RegistrySignal.renovation_type)
        .order_by(func.count(RegistrySignal.id).desc())
        .all()
    )
    query = (
        db.session.query(RegistrySignal, RegistryBuilding, RegistryHousingCompany)
        .join(RegistryBuilding, RegistrySignal.building_id == RegistryBuilding.id)
        .outerjoin(RegistryHousingCompany, RegistryBuilding.housing_company_id == RegistryHousingCompany.id)
    )
    if municipality:
        query = query.filter(func.lower(RegistryBuilding.municipality) == municipality.strip().lower())
    if kind:
        query = query.filter(RegistrySignal.kind == kind)
    sample = (
        query.order_by(
            RegistrySignal.confidence.desc(),
            RegistryHousingCompany.id.is_(None),
            RegistrySignal.due_year,
            RegistryBuilding.id,
        )
        .limit(limit)
        .all()
    )
    municipalities = [
        m
        for (m,) in db.session.query(RegistryBuilding.municipality)
        .filter(RegistryBuilding.municipality.isnot(None))
        .distinct()
        .order_by(RegistryBuilding.municipality)
    ]
    states = RegistrySyncState.query.filter(~RegistrySyncState.name.like("prh:%")).order_by(RegistrySyncState.name).all()
    prh_states = RegistrySyncState.query.filter(RegistrySyncState.name.like("prh:%"))
    return {
        "enabled": bool(current_app.config.get("REGISTRY_SYNC_ENABLED")),
        "scope": current_app.config.get("REGISTRY_MUNICIPALITIES") or "",
        "buildings": buildings,
        "with_address": with_address,
        "linked": linked,
        "companies": RegistryHousingCompany.query.count(),
        "permits": RegistryPermit.query.count(),
        "signals_by_kind": by_kind,
        "signals_by_type": by_type,
        "sample": sample,
        "municipalities": municipalities,
        "states": states,
        "prh_done": prh_states.filter(RegistrySyncState.last_success_at.isnot(None)).count(),
        "prh_errors": prh_states.filter(RegistrySyncState.last_error.isnot(None)).count(),
    }
