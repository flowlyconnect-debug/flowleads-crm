from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc

from app.extensions import db
from app.search.constants import ALL_FINLAND_REGION, BUILDING_TYPES, FINNISH_REGIONS, REMONTTITYYPIT
from app.search.models import SEARCH_SCHEDULES, SearchJob, SearchProfile

# "leave this field as it is" marker for update_profile
KEEP = object()

MAX_CITIES = 60
MIN_BUILD_YEAR = 1800
MAX_BUILD_YEAR = 2100
MAX_TIMING_YEARS = 30


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


def _optional_int(value, *, low: int, high: int, message: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise SearchProfileServiceError(message) from None
    if not low <= number <= high:
        raise SearchProfileServiceError(message)
    return number


def allowed_types(organization_id: int | None) -> tuple[str, ...]:
    """The toimialat an organization's profiles may use, in catalogue order.
    Everything when the organization is not restricted."""
    from app.users.models import Organization

    organization = db.session.get(Organization, organization_id) if organization_id else None
    if organization is None or organization.remonttityypit is None:
        return REMONTTITYYPIT
    return tuple(t for t in REMONTTITYYPIT if t in organization.remonttityypit)


def clean_types(values) -> list[str]:
    """Valid toimiala names in catalogue order, no duplicates."""
    chosen = {(v or "").strip() for v in (values or [])}
    unknown = chosen - set(REMONTTITYYPIT) - {""}
    if unknown:
        raise SearchProfileServiceError("Valitse toimialat listasta.")
    return [t for t in REMONTTITYYPIT if t in chosen]


def _check_entitlement(profile: SearchProfile) -> None:
    allowed = allowed_types(profile.organization_id)
    if not allowed:
        raise SearchProfileServiceError(
            "Organisaatiolle ei ole vielä määritetty toimialoja. Ota yhteyttä FlowLeadsiin.",
            "no_entitlement",
        )
    types = [t for t in (profile.remonttityypit or []) if t] or [profile.remonttityyppi]
    outside = [t for t in types if t not in allowed]
    if outside:
        raise SearchProfileServiceError(
            f"Toimiala ei kuulu organisaation tilaukseen: {', '.join(outside)}.",
            "type_not_allowed",
        )


def set_organization_types(organization, types) -> list[str]:
    """Set the toimialat a customer is entitled to and bring its profiles in
    line: a removed toimiala is dropped from every profile, a newly granted one
    is added, and what the customer had switched off stays off. Profiles are
    rematched by the scheduler."""
    new = clean_types(types)
    if not new:
        raise SearchProfileServiceError("Valitse vähintään yksi toimiala.")
    previous = organization.remonttityypit
    organization.remonttityypit = new
    for profile in SearchProfile.query.filter_by(organization_id=organization.id).all():
        current = [t for t in (profile.remonttityypit or []) if t] or [profile.remonttityyppi]
        kept = [t for t in new if t in current or (previous is not None and t not in previous)]
        kept = kept or list(new)
        if kept != current:
            profile.remonttityyppi = kept[0]
            profile.remonttityypit = kept
            profile.needs_rematch = True
            profile.updated_at = datetime.now(timezone.utc)
    return new


def normalize_types(remonttityyppi: str, remonttityypit) -> list[str]:
    """All wanted renovation types, the primary one first, no duplicates."""
    types: list[str] = []
    for value in [remonttityyppi, *(remonttityypit or [])]:
        value = (value or "").strip()
        if not value:
            continue
        if value not in REMONTTITYYPIT:
            raise SearchProfileServiceError("Valitse kelvollinen remonttityyppi.")
        if value not in types:
            types.append(value)
    if not types:
        raise SearchProfileServiceError("Valitse vähintään yksi remonttityyppi.")
    return types


def normalize_cities(cities) -> list[str]:
    """Accepts a list or a comma / newline separated string."""
    if cities is None:
        return []
    if isinstance(cities, str):
        cities = cities.replace("\n", ",").replace(";", ",").split(",")
    result: list[str] = []
    seen: set[str] = set()
    for raw in cities:
        city = " ".join(str(raw or "").split())
        if not city:
            continue
        if len(city) > 100:
            raise SearchProfileServiceError("Kaupungin nimi on liian pitkä.")
        key = city.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(city[:1].upper() + city[1:])
    if len(result) > MAX_CITIES:
        raise SearchProfileServiceError(f"Kaupunkeja voi olla enintään {MAX_CITIES}.")
    return result


def normalize_building_types(values) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if value not in BUILDING_TYPES:
            raise SearchProfileServiceError("Valitse talotyypit listasta.")
        if value not in result:
            result.append(value)
    # every type chosen is the same as no restriction
    return [] if len(result) == len(BUILDING_TYPES) else result


def _apply_filters(
    profile: SearchProfile,
    *,
    remonttityypit,
    cities,
    timing_within_years,
    build_year_min,
    build_year_max,
    building_types,
) -> None:
    if remonttityypit is not KEEP:
        types = normalize_types(profile.remonttityyppi, remonttityypit)
        profile.remonttityyppi = types[0]
        profile.remonttityypit = types
    elif profile.remonttityypit and profile.remonttityyppi not in profile.remonttityypit:
        # only the primary type was changed (old single-type callers)
        profile.remonttityypit = [profile.remonttityyppi]
    if cities is not KEEP:
        profile.cities = normalize_cities(cities)
    if timing_within_years is not KEEP:
        profile.timing_within_years = _optional_int(
            timing_within_years,
            low=0,
            high=MAX_TIMING_YEARS,
            message=f"Remontin ajankohta pitää olla 0–{MAX_TIMING_YEARS} vuotta.",
        )
    year_message = f"Rakennusvuosi pitää olla väliltä {MIN_BUILD_YEAR}–{MAX_BUILD_YEAR}."
    if build_year_min is not KEEP:
        profile.build_year_min = _optional_int(
            build_year_min, low=MIN_BUILD_YEAR, high=MAX_BUILD_YEAR, message=year_message
        )
    if build_year_max is not KEEP:
        profile.build_year_max = _optional_int(
            build_year_max, low=MIN_BUILD_YEAR, high=MAX_BUILD_YEAR, message=year_message
        )
    if (
        profile.build_year_min is not None
        and profile.build_year_max is not None
        and profile.build_year_min > profile.build_year_max
    ):
        raise SearchProfileServiceError("Rakennusvuoden alaraja ei voi olla ylärajaa suurempi.")
    if building_types is not KEEP:
        profile.building_types = normalize_building_types(building_types)


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


def build_profile(
    organization_id: int,
    *,
    name: str,
    remonttityyppi: str,
    regions: list[str],
    schedule_description: str = "daily",
    crm_api_key: str | None = None,
    is_active: bool = True,
    source: str = "oikotie",
    min_confidence: int = 60,
    daily_lead_limit: int = 50,
    remonttityypit=None,
    cities=None,
    timing_within_years=None,
    build_year_min=None,
    build_year_max=None,
    building_types=None,
) -> SearchProfile:
    """Validate and build a profile WITHOUT adding it to the session (used by
    create_profile and by the settings page's match preview)."""
    name = (name or "").strip()
    crm_api_key = (crm_api_key or "").strip() or None
    source = (source or "oikotie").strip() or "oikotie"
    remonttityyppi = (remonttityyppi or "").strip()
    if not remonttityyppi and remonttityypit:
        remonttityyppi = next((t for t in remonttityypit if (t or "").strip()), "")
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
    _apply_filters(
        profile,
        remonttityypit=remonttityypit or [],
        cities=cities,
        timing_within_years=timing_within_years,
        build_year_min=build_year_min,
        build_year_max=build_year_max,
        building_types=building_types,
    )
    _check_entitlement(profile)
    return profile


def create_profile(organization_id: int, **fields) -> SearchProfile:
    """Create a search profile: a filter over the shared listing catalogue.
    needs_rematch=True makes the scheduler collect its matches from the
    catalogue's recent history on the next `listing_matching` run."""
    profile = build_profile(organization_id, **fields)
    db.session.add(profile)
    return profile


def update_profile(
    profile: SearchProfile,
    *,
    name: str,
    remonttityyppi: str,
    regions: list[str],
    schedule_description: str = "daily",
    crm_api_key: str | None = None,
    is_active: bool,
    min_confidence: int | None = None,
    daily_lead_limit: int | None = None,
    remonttityypit=KEEP,
    cities=KEEP,
    timing_within_years=KEEP,
    build_year_min=KEEP,
    build_year_max=KEEP,
    building_types=KEEP,
) -> SearchProfile:
    name = (name or "").strip()
    source = profile.source or "oikotie"
    remonttityyppi = (remonttityyppi or "").strip()
    if not remonttityyppi and remonttityypit is not KEEP and remonttityypit:
        remonttityyppi = next((t for t in remonttityypit if (t or "").strip()), "")
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
    _apply_filters(
        profile,
        remonttityypit=remonttityypit,
        cities=cities,
        timing_within_years=timing_within_years,
        build_year_min=build_year_min,
        build_year_max=build_year_max,
        building_types=building_types,
    )
    _check_entitlement(profile)
    # filter may have changed: the scheduler rebuilds this profile's matches
    profile.needs_rematch = True
    profile.updated_at = datetime.now(timezone.utc)
    return profile


def activate_profile(profile: SearchProfile) -> SearchProfile:
    """Turn a profile on. An inactive profile has had its pending matches
    cleared (rematch_profile), so it must be rematched to pick up the
    catalogue's recent history again."""
    profile.is_active = True
    profile.needs_rematch = True
    profile.updated_at = datetime.now(timezone.utc)
    return profile


def delete_profile(profile: SearchProfile) -> None:
    from app.listings.models import ListingMatch

    ListingMatch.query.filter_by(profile_id=profile.id).delete(synchronize_session=False)
    db.session.delete(profile)


# ---------------------------------------------------------------------------
# Legacy per-profile SearchJob pipeline (SEARCH_JOBS_ENABLED=false). Nothing in
# onboarding or the settings UI creates jobs any more; kept for the old
# /api/v1/n8n/jobs code path and its tests until that is removed.
# ---------------------------------------------------------------------------


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
