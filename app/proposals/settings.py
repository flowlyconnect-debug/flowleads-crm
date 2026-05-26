from __future__ import annotations

from decimal import Decimal

from app.tasks.models import OrganizationSettings
from app.tasks.settings import get_organization_settings


def get_proposal_settings(organization_id: int) -> OrganizationSettings:
    return get_organization_settings(organization_id)


def get_default_tax_percent(organization_id: int) -> Decimal:
    settings = get_proposal_settings(organization_id)
    return Decimal(str(settings.proposal_default_tax_percent or 24))


def get_default_valid_days(organization_id: int) -> int:
    settings = get_proposal_settings(organization_id)
    return int(settings.proposal_default_valid_days or 30)
