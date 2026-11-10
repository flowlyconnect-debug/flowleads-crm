from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc

from app.extensions import db
from app.search.constants import ALL_FINLAND_REGION, FINNISH_REGIONS, REMONTTITYYPIT
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
    source: str,
) -> None:
    if not name or len(name) > 100:
        raise SearchProfileServiceError("Nimi on pakollinen (max 100 merkkiä).")
    if remonttityyppi not in REMONTTITYYPIT:
        raise SearchProfileServiceError("Valitse kelvollinen remonttityyppi.")
    if not regions:
        raise SearchProfileServiceError("Valitse vähintään yksi alue.")
    if ALL_FINLAND_REGION in regions:
        if len(regions) > 1:
            raise SearchProfileServiceError(
                "Koko Suomi valitaan omana valintana, ei yhdessä muiden alueiden kanssa."
            )
    else:
        invalid_regions = [region for region in regions if region not in FINNISH_REGIONS]
        if invalid_regions:
            raise SearchProfileServiceError("Valitse maakunnat listasta.")
    if schedule_description not in SEARCH_SCHEDULES:
        raise SearchProfileServiceError("Valitse kelvollinen aikataulu.")
    if crm_api_key is not None and len(crm_api_key) > 200:
        raise SearchProfileServiceError("CRM API-avain on liian pitkä.")
    if not source or len(source) > 50:
        raise SearchProfileServiceError("Valitse kelvollinen lähde.")


def _validate_min_confidence(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise SearchProfileServiceError("Vähimmäisvarmuus pitää olla luku 0–100.") from None
    if not 0 <= value <= 100:
        raise SearchProfileServiceError("Vähimmäisvarmuus pitää olla luku 0–100.")
    return value


def _validate_daily_lead_limit(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise SearchProfileServiceError("Päiväraja pitää olla luku 0–1000.") from None
    if not 0 <= value <= 1000:
        raise SearchProfileServiceError("Päiväraja pitää olla luku 0–1000 (0 = ei rajaa).")
    return value


def mask_crm_api_key_display(key: str | None) -> str | None:
    if not key or not str(key).strip():
        return None
    value = str(key).strip()
    prefix_len = min(12, max(4, len(value) - 4))
    return f"{value[:prefix_len]}...****"


def _resolve_crm_api_key_for_update(
    profile: SearchProfile,
    crm_api_key: str | None,
) -> str | None:
    if crm_api_key is None:
        return profile.crm_api_key
    stripped = str(crm_api_key).strip()
    if not stripped:
        return profile.crm_api_key
    return stripped


def create_profile(
    organization_id: int,
    *,
    name: str,
    remonttityyppi: str,
    regions: list[str],
    schedule_description: str,
    crm_api_key: str | None,
    is_active: bool,
    source: str = "oikotie",
    min_confidence: int = 60,
    daily_lead_limit: int = 50,
) -> SearchProfile:
    name = (name or "").strip()
    crm_api_key = (crm_api_key or "").strip() or None
    source = (source or "oikotie").strip() or "oikotie"
    _validate_profile_fields(
        name=name,
        remonttityyppi=remonttityyppi,
        regions=regions,
        schedule_description=schedule_description,
        crm_api_key=crm_api_key,
        source=source,
    )
    profile = SearchProfile(
        organization_id=organization_id,
        name=name,
        remonttityyppi=remonttityyppi,
        regions=list(regions),
        source=source,
        schedule_description=schedule_description,
        crm_api_key=crm_api_key,
        is_active=is_active,
        min_confidence=_validate_min_confidence(min_confidence),
        daily_lead_limit=_validate_daily_lead_limit(daily_lead_limit),
        needs_rematch=True,
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
    min_confidence: int | None = None,
    daily_lead_limit: int | None = None,
) -> SearchProfile:
    name = (name or "").strip()
    source = profile.source or "oikotie"
    resolved_crm_api_key = _resolve_crm_api_key_for_update(profile, crm_api_key)
    _validate_profile_fields(
        name=name,
        remonttityyppi=remonttityyppi,
        regions=regions,
        schedule_description=schedule_description,
        crm_api_key=resolved_crm_api_key,
        source=source,
    )
    profile.name = name
    profile.remonttityyppi = remonttityyppi
    profile.regions = list(regions)
    profile.schedule_description = schedule_description
    profile.crm_api_key = resolved_crm_api_key
    profile.is_active = is_active
    if min_confidence is not None:
        profile.min_confidence = _validate_min_confidence(min_confidence)
    if daily_lead_limit is not None:
        profile.daily_lead_limit = _validate_daily_lead_limit(daily_lead_limit)
    # filter may have changed: the scheduler rebuilds this profile's matches
    profile.needs_rematch = True
    profile.updated_at = datetime.now(timezone.utc)
    return profile


def delete_profile(profile: SearchProfile) -> None:
    from app.listings.models import ListingMatch

    ListingMatch.query.filter_by(profile_id=profile.id).delete(synchronize_session=False)
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
