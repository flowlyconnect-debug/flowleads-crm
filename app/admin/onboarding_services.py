from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from flask import current_app

from app.api.services import APIKeyServiceError, create_api_key
from app.core.audit import log_audit
from app.extensions import db
from app.search.profile_services import SearchProfileServiceError, create_profile, create_test_search_job
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
    search_profile_name: str
    api_key_full: str
    temporary_password: str | None
    initial_job_created: bool


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
    admin_name: str | None,
    search_profile_name: str,
    regions: list[str],
    remonttityyppi: str,
    source: str = "oikotie",
    is_active: bool = True,
    slug: str | None = None,
    create_initial_job: bool = True,
) -> CustomerOnboardingResult:
    organization_name = (organization_name or "").strip()
    if not organization_name:
        raise CustomerOnboardingError("Organisaation nimi on pakollinen.", "invalid_name")

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

        admin_user = create_user(
            admin_email,
            password,
            role="admin",
            organization_id=organization.id,
            actor=actor,
            is_active=is_active,
        )

        _api_key, api_key_full = create_api_key(
            organization.id,
            "n8n",
            created_by=actor.id,
            test_mode=bool(current_app.config.get("TESTING")),
        )

        profile = create_profile(
            organization.id,
            name=search_profile_name,
            remonttityyppi=remonttityyppi,
            regions=regions,
            schedule_description="daily",
            crm_api_key=api_key_full,
            is_active=is_active,
            source=source,
        )
        db.session.flush()

        initial_job_created = False
        if create_initial_job and is_active:
            create_test_search_job(organization.id, profile.id)
            initial_job_created = True

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
                "initial_job_created": initial_job_created,
            },
        )
    except UserServiceError as exc:
        raise CustomerOnboardingError(exc.message, exc.code) from exc
    except APIKeyServiceError as exc:
        raise CustomerOnboardingError(exc.message, exc.code) from exc
    except SearchProfileServiceError as exc:
        raise CustomerOnboardingError(exc.message, exc.code) from exc

    return CustomerOnboardingResult(
        organization=organization,
        admin_user=admin_user,
        search_profile_name=profile.name,
        api_key_full=api_key_full,
        temporary_password=generated_password,
        initial_job_created=initial_job_created,
    )
