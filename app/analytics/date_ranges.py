from datetime import date, datetime, timedelta, timezone

VALID_RANGES = frozenset({"this_week", "this_month", "last_30_days", "custom"})
MAX_CUSTOM_RANGE_DAYS = 366


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _start_of_day(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _end_of_day(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)


def parse_date_param(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def resolve_report_dates(
    range_key: str,
    *,
    start: str | None = None,
    end: str | None = None,
) -> tuple[datetime, datetime, str | None]:
    """Return (start_dt, end_dt inclusive, error_message)."""
    range_key = range_key or "this_month"
    if range_key not in VALID_RANGES:
        return _utc_now(), _utc_now(), f"Invalid range '{range_key}'."

    today = _utc_now().date()

    if range_key == "this_week":
        start_d = today - timedelta(days=today.weekday())
        end_d = today
    elif range_key == "this_month":
        start_d = today.replace(day=1)
        end_d = today
    elif range_key == "last_30_days":
        start_d = today - timedelta(days=29)
        end_d = today
    else:
        start_d = parse_date_param(start)
        end_d = parse_date_param(end)
        if start_d is None or end_d is None:
            return _utc_now(), _utc_now(), "Custom range requires both start and end dates (YYYY-MM-DD)."
        if end_d < start_d:
            return _utc_now(), _utc_now(), "End date must be on or after start date."
        if (end_d - start_d).days > MAX_CUSTOM_RANGE_DAYS:
            return (
                _utc_now(),
                _utc_now(),
                f"Custom range cannot exceed {MAX_CUSTOM_RANGE_DAYS} days.",
            )

    return _start_of_day(start_d), _end_of_day(end_d), None
