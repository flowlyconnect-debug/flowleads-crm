"""Organization currency helpers for deal value display."""

from __future__ import annotations

from app.analytics.models import ALLOWED_CURRENCIES
from app.tasks.settings import get_organization_settings


def get_default_currency(organization_id: int) -> str:
    settings = get_organization_settings(organization_id)
    currency = (settings.default_currency or "EUR").upper()
    if currency not in ALLOWED_CURRENCIES:
        return "EUR"
    return currency


def currency_symbol(currency: str) -> str:
    symbols = {"EUR": "€", "USD": "$", "SEK": "kr", "GBP": "£"}
    return symbols.get(currency.upper(), currency)
