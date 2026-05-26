from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.extensions import db
from app.tasks.settings import get_organization_settings

MONEY_QUANT = Decimal("0.01")


def money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def line_item_total(
    quantity: Decimal | float | int | str,
    unit_price: Decimal | float | int | str,
    discount_percent: Decimal | float | int | str = 0,
) -> Decimal:
    qty = Decimal(str(quantity))
    price = Decimal(str(unit_price))
    sub = qty * price
    disc_pct = Decimal(str(discount_percent or 0))
    if disc_pct > 0:
        sub = sub - (sub * disc_pct / Decimal("100"))
    return money(sub)


def generate_reference_number(organization_id: int) -> str:
    """FLW-{YEAR}-{SEQUENCE} per organization, sequence resets each year."""
    settings = get_organization_settings(organization_id)
    year = datetime.now(timezone.utc).year
    key = f"proposal_sequence_{year}"
    seq_map = dict(settings.proposal_sequence_json or {})
    current = int(seq_map.get(key, 0)) + 1
    seq_map[key] = current
    settings.proposal_sequence_json = seq_map
    db.session.flush()
    return f"FLW-{year}-{current:03d}"


def get_sequence_for_year(organization_id: int, year: int) -> int:
    settings = get_organization_settings(organization_id)
    key = f"proposal_sequence_{year}"
    return int((settings.proposal_sequence_json or {}).get(key, 0))
