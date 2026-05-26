"""Safe server-side parsing of relative date placeholders like {{now-14d}}."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

RELATIVE_DATE_RE = re.compile(
    r"^\{\{now([+-])(\d+)([dhm])\}\}$",
    re.IGNORECASE,
)

_UNIT_DELTAS = {
    "d": lambda n: timedelta(days=n),
    "h": lambda n: timedelta(hours=n),
    "m": lambda n: timedelta(minutes=n),
}


def is_relative_date_token(value: str) -> bool:
    return bool(value and isinstance(value, str) and RELATIVE_DATE_RE.match(value.strip()))


def parse_relative_date(value: str, *, now: datetime | None = None) -> datetime:
    """Parse {{now-14d}}, {{now+7d}}, {{now-2h}}, {{now+30m}} into UTC datetime."""
    if not isinstance(value, str):
        raise ValueError("Relative date must be a string.")

    match = RELATIVE_DATE_RE.match(value.strip())
    if not match:
        raise ValueError(f"Invalid relative date token: {value}")

    sign, amount_str, unit = match.groups()
    amount = int(amount_str)
    if amount > 3650:
        raise ValueError("Relative date offset too large.")

    delta = _UNIT_DELTAS[unit.lower()](amount)
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    if sign == "-":
        return base - delta
    return base + delta


def resolve_filter_datetime(value) -> datetime | None:
    """Resolve ISO datetime strings or relative tokens to UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not isinstance(value, str):
        raise ValueError("Date filter value must be a string or datetime.")

    text = value.strip()
    if is_relative_date_token(text):
        return parse_relative_date(text)

    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
