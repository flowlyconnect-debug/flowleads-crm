"""Customer invite + self-service onboarding.

Flow:
1. Superadmin calls create_invite(): creates the Organization (no admin user,
   no search profile yet) and a CustomerInvite with a 7-day single-use token.
2. The customer clicks the emailed link, GET /invite/<token> shows a form.
3. accept_invite(): sets the admin's password, creates the SearchProfile with
   the regions/"Koko Suomi" they chose, and records which of the allowed
   features they turned on.

The profile is created ACTIVE. It is only a filter over the shared listing
catalogue (docs/ARKKITEHTUURI_KOHDEKATALOGI.md), so nothing is scraped per
customer and there is nothing to confirm before turning it on: the customer
gets leads from the catalogue's recent history within the next scheduler run.
activate_source() remains for profiles that were created or switched off as
inactive.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app

from app.admin.constants import FEATURE_CATALOG
from app.admin.models import INVITE_DEFAULT_TTL_DAYS, CustomerInvite
from app.core.audit import log_audit
from app.core.security import validate_password
from app.extensions import db
from app.search.models import SearchProfile
from app.search.profile_services import SearchProfileServiceError, activate_profile, create_profile
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
    search_profile_name: str | None = None,
    remonttityyppi: str | None = None,
    remonttityypit: list[str] | None = None,
    source: str = "oikotie",
    allowed_features: list[str] | None = None,
    slug: str | None = None,
    ttl_days: int = INVITE_DEFAULT_TTL_DAYS,
    trial: bool = False,
) -> CustomerInviteResult:
    organization_name = (organization_name or "").strip()
    if not organization_name:
        raise InviteServiceError("Organisaation nimi on pakollinen.", "invalid_name")

    # Validate everything before the organization is created: a bad invite
    # used to leave an organization behind that nobody could ever join.
    from app.core.security import normalize_email, validate_email
    from app.search.constants import REMONTTITYYPIT
    from app.users.services import get_user_by_email

    admin_email = normalize_email(admin_email or "")
    if not admin_email or not validate_email(admin_email):
        raise InviteServiceError("Anna kelvollinen sähköpostiosoite.", "invalid_email")
    if get_user_by_email(admin_email) is not None:
        raise InviteServiceError(
            "Tällä sähköpostiosoitteella on jo käyttäjätili.", "email_taken"
        )
    from app.admin.onboarding_services import _slug_from_name, clean_renovation_types, default_profile_name

    types = clean_renovation_types(remonttityypit or ([remonttityyppi] if remonttityyppi else []))
    if not types or (remonttityyppi and not remonttityypit and remonttityyppi not in REMONTTITYYPIT):
        raise InviteServiceError("Valitse kelvollinen remonttityyppi.", "invalid_type")
    search_profile_name = (search_profile_name or "").strip() or default_profile_name(types)

    org_slug = (slug or "").strip().lower() or _slug_from_name(organization_name)
    source = (source or "oikotie").strip() or "oikotie"

    try:
        organization = create_organization(organization_name, org_slug)
    except UserServiceError as exc:
        raise InviteServiceError(exc.message, exc.code) from exc
    if trial:
        # the trial clock starts when the customer accepts (accept_invite)
        organization.subscription_status = "trial"
        organization.trial_ends_at = None

    invite, token = _issue_invite_row(
        organization=organization,
        email=admin_email,
        invited_by=actor,
        search_profile_name=search_profile_name,
        remonttityyppi="; ".join(types),
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
    regions: list[str] | None = None,
    enabled_features: list[str] | None = None,
) -> InviteAcceptanceResult:
    """The customer only sets a password: the profile covers the whole country
    (regions are narrowed with the Kohteet filters, or later in settings)."""
    from app.search.constants import ALL_FINLAND_REGION

    regions = list(regions or []) or [ALL_FINLAND_REGION]
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

        profile = create_profile(
            organization.id,
            name=invite.search_profile_name,
            remonttityyppi=invite.types[0],
            remonttityypit=invite.types,
            regions=regions,
            is_active=True,
            source=invite.source,
        )

        organization.enabled_features = chosen_features
        invite.accepted_at = datetime.now(timezone.utc)
        if organization.subscription_status == "trial" and organization.trial_ends_at is None:
            from app.billing.services import start_trial

            start_trial(organization, now=invite.accepted_at)
        db.session.flush()
        from app.sequences.defaults import ensure_default_sequences

        ensure_default_sequences(organization.id, created_by=admin_user.id)

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
    except SearchProfileServiceError as exc:
        raise InviteServiceError(exc.message, exc.code) from exc

    from flask import url_for

    from app.email.services import EmailService

    EmailService.send_onboarding_complete_email(
        to_email=admin_user.email,
        organization_name=organization.name,
        login_url=url_for("auth.login", _external=True),
    )

    return InviteAcceptanceResult(
        organization=organization,
        admin_user=admin_user,
        search_profile=profile,
    )


def activate_source(profile: SearchProfile, *, actor: User) -> SearchProfile:
    """Turn an inactive profile on. The scheduler then rebuilds its matches
    from the catalogue's recent history (no per-customer search job exists)."""
    if profile.is_active:
        raise InviteServiceError("Hakuprofiili on jo aktiivinen.", "already_active")

    activate_profile(profile)
    db.session.flush()

    log_audit(
        "source_activated",
        user_id=actor.id,
        organization_id=profile.organization_id,
        target_type="search_profile",
        target_id=profile.id,
        metadata={"source": profile.source},
    )
    return profile
