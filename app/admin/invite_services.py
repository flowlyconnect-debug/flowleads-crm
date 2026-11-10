"""Customer invite + self-service onboarding.

Flow:
1. Superadmin calls create_invite(): creates the Organization (no admin user,
   no search profile yet) and a CustomerInvite with a 7-day single-use token.
2. The customer clicks the emailed link, GET /invite/<token> shows a form.
3. accept_invite(): sets the admin's password, creates the SearchProfile with
   the regions/"Koko Suomi" they chose, and records which of the allowed
   features they turned on. The profile is created INACTIVE — no search job
   is scheduled yet.
4. A superadmin later calls activate_source() to turn the profile on and
   create the first search job — a deliberate separate step, not automatic,
   since a brand-new source/customer pairing may still need confirming
   (contract, API access) before any real scraping starts.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app

from app.admin.constants import FEATURE_CATALOG
from app.admin.models import INVITE_DEFAULT_TTL_DAYS, CustomerInvite
from app.api.services import APIKeyServiceError, create_api_key
from app.core.audit import log_audit
from app.core.security import validate_password
from app.extensions import db
from app.search.models import SearchJob, SearchProfile
from app.search.profile_services import SearchProfileServiceError, create_profile, create_test_search_job
from app.users.models import Organization, User
from app.users.services import UserServiceError, create_organization, create_user


class InviteServiceError(Exception):
    def __init__(self, message: str, code: str = "invite_error"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class CustomerInviteResult:
    organization: Organization
    invite: CustomerInvite
    token: str


@dataclass
class InviteAcceptanceResult:
    organization: Organization
    admin_user: User
    search_profile: SearchProfile
    api_key_full: str


def _hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _generate_invite_token() -> str:
    return secrets.token_urlsafe(32)


def valid_feature_keys(features: list[str] | None) -> list[str]:
    if not features:
        return []
    seen = []
    for key in features:
        if key in FEATURE_CATALOG and key not in seen:
            seen.append(key)
    return seen


def create_invite(
    *,
    actor: User,
    organization_name: str,
    admin_email: str,
    search_profile_name: str,
    remonttityyppi: str,
    source: str = "oikotie",
    allowed_features: list[str] | None = None,
    slug: str | None = None,
    ttl_days: int = INVITE_DEFAULT_TTL_DAYS,
) -> CustomerInviteResult:
    organization_name = (organization_name or "").strip()
    if not organization_name:
        raise InviteServiceError("Organisaation nimi on pakollinen.", "invalid_name")

    from app.admin.onboarding_services import _slug_from_name

    org_slug = (slug or "").strip().lower() or _slug_from_name(organization_name)
    source = (source or "oikotie").strip() or "oikotie"

    try:
        organization = create_organization(organization_name, org_slug)
    except UserServiceError as exc:
        raise InviteServiceError(exc.message, exc.code) from exc

    invite, token = _issue_invite_row(
        organization=organization,
        email=admin_email,
        invited_by=actor,
        search_profile_name=search_profile_name,
        remonttityyppi=remonttityyppi,
        source=source,
        allowed_features=valid_feature_keys(allowed_features),
        ttl_days=ttl_days,
    )

    log_audit(
        "customer_invited",
        user_id=actor.id,
        organization_id=organization.id,
        target_type="customer_invite",
        target_id=invite.id,
        metadata={"email": invite.email, "organization_name": organization.name},
    )

    return CustomerInviteResult(organization=organization, invite=invite, token=token)


def renew_invite(invite: CustomerInvite, *, actor: User, ttl_days: int = INVITE_DEFAULT_TTL_DAYS) -> CustomerInviteResult:
    """Revoke the given invite (its link stops working) and issue a fresh one
    for the same organization/email/profile settings."""
    invite.revoked_at = datetime.now(timezone.utc)

    new_invite, token = _issue_invite_row(
        organization=invite.organization,
        email=invite.email,
        invited_by=actor,
        search_profile_name=invite.search_profile_name,
        remonttityyppi=invite.remonttityyppi,
        source=invite.source,
        allowed_features=invite.allowed_features,
        ttl_days=ttl_days,
    )

    log_audit(
        "customer_invite_renewed",
        user_id=actor.id,
        organization_id=invite.organization_id,
        target_type="customer_invite",
        target_id=new_invite.id,
        metadata={"email": new_invite.email, "previous_invite_id": invite.id},
    )
    return CustomerInviteResult(organization=invite.organization, invite=new_invite, token=token)


def _issue_invite_row(
    *,
    organization: Organization,
    email: str,
    invited_by: User,
    search_profile_name: str,
    remonttityyppi: str,
    source: str,
    allowed_features: list[str],
    ttl_days: int,
) -> tuple[CustomerInvite, str]:
    email = (email or "").strip().lower()
    if not email:
        raise InviteServiceError("Sähköposti on pakollinen.", "invalid_email")
    search_profile_name = (search_profile_name or "").strip()
    if not search_profile_name:
        raise InviteServiceError("Hakuprofiilin nimi on pakollinen.", "invalid_profile_name")

    now = datetime.now(timezone.utc)
    token = _generate_invite_token()
    invite = CustomerInvite(
        organization_id=organization.id,
        email=email,
        token_hash=_hash_invite_token(token),
        allowed_features=list(allowed_features),
        search_profile_name=search_profile_name,
        remonttityyppi=remonttityyppi,
        source=source,
        invited_by_user_id=invited_by.id,
        expires_at=now + timedelta(days=ttl_days),
    )
    db.session.add(invite)
    db.session.flush()
    return invite, token


def get_valid_invite(token: str | None) -> CustomerInvite | None:
    if not token:
        return None
    invite = CustomerInvite.query.filter_by(token_hash=_hash_invite_token(token)).first()
    if invite is None or not invite.is_valid():
        return None
    return invite


def accept_invite(
    invite: CustomerInvite,
    *,
    password: str,
    regions: list[str],
    enabled_features: list[str] | None,
) -> InviteAcceptanceResult:
    if not invite.is_valid():
        raise InviteServiceError("Kutsu ei ole enää voimassa.", "invite_invalid")

    ok, msg = validate_password(password)
    if not ok:
        raise InviteServiceError(msg or "Salasana ei kelpaa.", "invalid_password")

    organization = invite.organization
    chosen_features = [f for f in valid_feature_keys(enabled_features) if f in (invite.allowed_features or [])]

    try:
        admin_user = create_user(
            invite.email,
            password,
            role="admin",
            organization_id=organization.id,
            is_active=True,
        )

        _api_key, api_key_full = create_api_key(
            organization.id,
            "n8n",
            created_by=admin_user.id,
            test_mode=bool(current_app.config.get("TESTING")),
        )

        # Onboarding sets the regions/profile up but never auto-activates the
        # source — a superadmin must explicitly activate it (see
        # activate_source below) before any search job is scheduled.
        profile = create_profile(
            organization.id,
            name=invite.search_profile_name,
            remonttityyppi=invite.remonttityyppi,
            regions=regions,
            schedule_description="daily",
            crm_api_key=api_key_full,
            is_active=False,
            source=invite.source,
        )

        organization.enabled_features = chosen_features
        invite.accepted_at = datetime.now(timezone.utc)
        db.session.flush()

        log_audit(
            "customer_invite_accepted",
            user_id=admin_user.id,
            organization_id=organization.id,
            target_type="organization",
            target_id=organization.id,
            metadata={
                "regions": regions,
                "enabled_features": chosen_features,
                "search_profile_id": profile.id,
            },
        )
    except UserServiceError as exc:
        raise InviteServiceError(exc.message, exc.code) from exc
    except APIKeyServiceError as exc:
        raise InviteServiceError(exc.message, exc.code) from exc
    except SearchProfileServiceError as exc:
        raise InviteServiceError(exc.message, exc.code) from exc

    return InviteAcceptanceResult(
        organization=organization,
        admin_user=admin_user,
        search_profile=profile,
        api_key_full=api_key_full,
    )


def activate_source(profile: SearchProfile, *, actor: User) -> SearchJob:
    """Superadmin-only step: turn the (inactive) onboarded profile on and
    create its first search job. Separate from accept_invite on purpose."""
    if profile.is_active:
        raise InviteServiceError("Lähde on jo aktivoitu.", "already_active")

    profile.is_active = True
    db.session.flush()
    job = create_test_search_job(profile.organization_id, profile.id)

    log_audit(
        "source_activated",
        user_id=actor.id,
        organization_id=profile.organization_id,
        target_type="search_profile",
        target_id=profile.id,
        metadata={"source": profile.source},
    )
    return job
