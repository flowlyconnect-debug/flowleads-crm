from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from flask import current_app

from app.core.audit import log_audit
from app.extensions import db
from app.search.models import SearchProfile
from app.search.profile_services import SearchProfileServiceError, create_profile
from app.users.models import Organization, User
from app.users.services import UserServiceError, create_organization, create_user


class CustomerOnboardingError(Exception):
    def __init__(self, message: str, code: str = "onboarding_error"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class CustomerOnboardingResult:
    organization: Organization
    admin_user: User
    search_profile: SearchProfile
    temporary_password: str | None

    @property
    def search_profile_name(self) -> str:
        return self.search_profile.name


_FINNISH_SLUG_MAP = str.maketrans(
    {
        "ä": "a",
        "ö": "o",
        "å": "a",
        "Ä": "a",
        "Ö": "o",
        "Å": "a",
    }
)


def _slug_from_name(name: str) -> str:
    from app.core.security import validate_slug

    value = name.strip().translate(_FINNISH_SLUG_MAP).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    if not value:
        value = "asiakas"
    if len(value) > 120:
        value = value[:120].rstrip("-")
    base = value
    candidate = value
    counter = 2
    while Organization.query.filter_by(slug=candidate).first() is not None:
        suffix = f"-{counter}"
        candidate = f"{base[: 120 - len(suffix)]}{suffix}"
        counter += 1
    if not validate_slug(candidate):
        raise CustomerOnboardingError("Organisaation slugia ei voitu muodostaa.", "invalid_slug")
    return candidate


def clean_renovation_types(values) -> list[str]:
    """The chosen renovation types (urakat), in catalogue order, or []."""
    from app.search.constants import REMONTTITYYPIT

    if isinstance(values, str):
        values = [v for v in values.split(";")]
    chosen = {str(v).strip() for v in (values or []) if str(v).strip()}
    return [t for t in REMONTTITYYPIT if t in chosen]


def default_profile_name(types: list[str]) -> str:
    """'Putkiremontti, Julkisivuremontti' (max 100 chars) when the admin left
    the profile name empty."""
    name = ", ".join(types) or "Hakuprofiili"
    return name if len(name) <= 100 else name[:97].rstrip(", ") + "…"


def _generate_temporary_password() -> str:
    min_len = current_app.config.get("PASSWORD_MIN_LENGTH", 12)
    while True:
        password = secrets.token_urlsafe(16)
        if len(password) >= min_len:
            return password


def create_customer(
    *,
    actor: User,
    organization_name: str,
    admin_email: str,
    admin_password: str | None,
    admin_name: str | None = None,
    search_profile_name: str | None = None,
    regions: list[str] | None = None,
    remonttityyppi: str | None = None,
    remonttityypit: list[str] | None = None,
    source: str = "oikotie",
    is_active: bool = True,
    slug: str | None = None,
    min_confidence: int = 50,
    daily_lead_limit: int = 50,
    cities: list[str] | None = None,
    trial: bool = False,
) -> CustomerOnboardingResult:
    """Create organization + admin + search profile in one go.

    The profile is a filter over the shared listing catalogue
    (docs/ARKKITEHTUURI_KOHDEKATALOGI.md): nothing is scheduled per customer.
    An active profile gets its matches from the last 30 days of the catalogue
    on the next `listing_matching` scheduler run (needs_rematch=True).
    The admin must change the (temporary) password on first login.
    """
    organization_name = (organization_name or "").strip()
    if not organization_name:
        raise CustomerOnboardingError("Organisaation nimi on pakollinen.", "invalid_name")
    types = clean_renovation_types(remonttityypit or ([remonttityyppi] if remonttityyppi else []))
    if not types:
        raise CustomerOnboardingError("Valitse vähintään yksi remonttityyppi.", "invalid_type")
    # No region choice at onboarding: the customer sees the whole country and
    # narrows the list on the Kohteet page (region / city filters).
    from app.search.constants import ALL_FINLAND_REGION

    regions = list(regions or []) or [ALL_FINLAND_REGION]
    search_profile_name = (search_profile_name or "").strip() or default_profile_name(types)

    org_slug = (slug or "").strip().lower() or _slug_from_name(organization_name)

    password = (admin_password or "").strip() or None
    generated_password: str | None = None
    if not password:
        password = _generate_temporary_password()
        generated_password = password

    source = (source or "oikotie").strip() or "oikotie"
    if len(source) > 50:
        raise CustomerOnboardingError("Lähde on liian pitkä.", "invalid_source")

    try:
        organization = create_organization(organization_name, org_slug)
        organization.is_active = is_active
        if trial:
            from app.billing.services import start_trial

            start_trial(organization)  # app/billing: free trial, then the monthly contract

        admin_user = create_user(
            admin_email,
            password,
            role="admin",
            organization_id=organization.id,
            actor=actor,
            is_active=is_active,
        )

        admin_user.must_change_password = True

        profile = create_profile(
            organization.id,
            name=search_profile_name,
            remonttityyppi=types[0],
            remonttityypit=types,
            regions=regions,
            schedule_description="daily",
            crm_api_key=None,
            is_active=is_active,
            source=source,
            min_confidence=min_confidence,
            daily_lead_limit=daily_lead_limit,
            cities=cities,
        )
        db.session.flush()

        log_audit(
            "customer_onboarded",
            user_id=actor.id,
            organization_id=organization.id,
            target_type="organization",
            target_id=organization.id,
            metadata={
                "admin_email": admin_user.email,
                "admin_name": (admin_name or "").strip() or None,
                "search_profile_name": profile.name,
                "search_profile_id": profile.id,
                "source": source,
                "is_active": is_active,
            },
        )
    except UserServiceError as exc:
        raise CustomerOnboardingError(exc.message, exc.code) from exc
    except SearchProfileServiceError as exc:
        raise CustomerOnboardingError(exc.message, exc.code) from exc

    return CustomerOnboardingResult(
        organization=organization,
        admin_user=admin_user,
        search_profile=profile,
        temporary_password=generated_password,
    )
