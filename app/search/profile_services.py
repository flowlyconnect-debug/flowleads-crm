from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc

from app.extensions import db
from app.search.constants import FINNISH_REGIONS, REMONTTITYYPIT
from app.search.models import SEARCH_SCHEDULES, SearchJob, SearchProfile


class SearchProfileServiceError(Exception):
    def __init__(self, message: str, code: str = "validation_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def list_profiles(organization_id: int) -> list[SearchProfile]:
    return (
        SearchProfile.query.filter_by(organization_id=organization_id)
        .order_by(SearchProfile.name.asc(), SearchProfile.id.asc())
        .all()
    )


def get_profile(organization_id: int, profile_id: int) -> SearchProfile | None:
    return SearchProfile.query.filter_by(
        id=profile_id,
        organization_id=organization_id,
    ).first()


def get_latest_job(profile_id: int) -> SearchJob | None:
    return (
        SearchJob.query.filter_by(search_profile_id=profile_id)
        .order_by(desc(SearchJob.scheduled_at), desc(SearchJob.id))
        .first()
    )


def _validate_profile_fields(
    *,
    name: str,
    remonttityyppi: str,
    regions: list[str],
    schedule_description: str,
    crm_api_key: str | None,
) -> None:
    if not name or len(name) > 100:
        raise SearchProfileServiceError("Nimi on pakollinen (max 100 merkkiä).")
    if remonttityyppi not in REMONTTITYYPIT:
        raise SearchProfileServiceError("Valitse kelvollinen remonttityyppi.")
    if not regions:
        raise SearchProfileServiceError("Valitse vähintään yksi alue.")
    invalid_regions = [region for region in regions if region not in FINNISH_REGIONS]
    if invalid_regions:
        raise SearchProfileServiceError("Valitse maakunnat listasta.")
    if schedule_description not in SEARCH_SCHEDULES:
        raise SearchProfileServiceError("Valitse kelvollinen aikataulu.")
    if crm_api_key is not None and len(crm_api_key) > 200:
        raise SearchProfileServiceError("CRM API-avain on liian pitkä.")


def create_profile(
    organization_id: int,
    *,
    name: str,
    remonttityyppi: str,
    regions: list[str],
    schedule_description: str,
    crm_api_key: str | None,
    is_active: bool,
) -> SearchProfile:
    name = (name or "").strip()
    crm_api_key = (crm_api_key or "").strip() or None
    _validate_profile_fields(
        name=name,
        remonttityyppi=remonttityyppi,
        regions=regions,
        schedule_description=schedule_description,
        crm_api_key=crm_api_key,
    )
    profile = SearchProfile(
        organization_id=organization_id,
        name=name,
        remonttityyppi=remonttityyppi,
        regions=list(regions),
        schedule_description=schedule_description,
        crm_api_key=crm_api_key,
        is_active=is_active,
    )
    db.session.add(profile)
    return profile


def update_profile(
    profile: SearchProfile,
    *,
    name: str,
    remonttityyppi: str,
    regions: list[str],
    schedule_description: str,
    crm_api_key: str | None,
    is_active: bool,
) -> SearchProfile:
    name = (name or "").strip()
    crm_api_key = (crm_api_key or "").strip() or None
    _validate_profile_fields(
        name=name,
        remonttityyppi=remonttityyppi,
        regions=regions,
        schedule_description=schedule_description,
        crm_api_key=crm_api_key,
    )
    profile.name = name
    profile.remonttityyppi = remonttityyppi
    profile.regions = list(regions)
    profile.schedule_description = schedule_description
    profile.crm_api_key = crm_api_key
    profile.is_active = is_active
    profile.updated_at = datetime.now(timezone.utc)
    return profile


def delete_profile(profile: SearchProfile) -> None:
    db.session.delete(profile)


def has_active_job(profile_id: int) -> bool:
    return (
        SearchJob.query.filter(
            SearchJob.search_profile_id == profile_id,
            SearchJob.status.in_(["pending", "running"]),
        ).first()
        is not None
    )


def create_test_search_job(organization_id: int, profile_id: int) -> SearchJob:
    profile = get_profile(organization_id, profile_id)
    if not profile:
        raise SearchProfileServiceError("Hakuprofiilia ei löytynyt.", "not_found")
    if not profile.is_active:
        raise SearchProfileServiceError(
            "Vain aktiivinen hakuprofiili voi saada testijobin.",
            "inactive_profile",
        )
    if has_active_job(profile.id):
        raise SearchProfileServiceError(
            "Profiililla on jo odottava tai käynnissä oleva haku.",
            "job_exists",
        )

    now = datetime.now(timezone.utc)
    job = SearchJob(
        search_profile_id=profile.id,
        organization_id=organization_id,
        status="pending",
        scheduled_at=now,
    )
    db.session.add(job)
    return job
