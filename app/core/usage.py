"""Daily usage counters per customer user (`UsageDaily`), read by the
superadmin Asiakasseuranta (`app/admin/activity_services.py`).

`track("company_card_view")` adds one to today's row of the current user.
Superadmins are never counted: opening a customer's pages to help them must
not look like the customer's own activity. Tracking never breaks the page:
any error is logged and swallowed."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import has_request_context
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db

logger = logging.getLogger(__name__)

# what is counted; the labels are the Finnish names on Asiakasseuranta
METRICS = {
    "targets_view": "Kohteet-sivu",
    "company_card_view": "Taloyhtiökortti",
    "manager_view": "Isännöitsijä",
    "map_view": "Kartta",
    "lead_view": "Liidin sivu",
    "targets_transferred": "Siirretty liidiksi",
    "leads_exported": "Liidejä viety CSV:nä",
    "crm_exported": "Liidejä viety CRM:ään",
    "api_leads_read": "Liidejä luettu API:lla",
}
# page views of contact data
VIEW_METRICS = ("targets_view", "company_card_view", "manager_view", "map_view", "lead_view")
# rows that left FlowLeads
EXPORT_METRICS = ("leads_exported", "crm_exported", "api_leads_read")


def _current_user():
    if not has_request_context():
        return None
    try:
        from flask_login import current_user

        return current_user if current_user and current_user.is_authenticated else None
    except Exception:  # noqa: BLE001 - no login manager (CLI, tests)
        return None


def track(
    metric: str,
    amount: int = 1,
    *,
    organization_id: int | None = None,
    user_id: int | None = None,
    commit: bool = True,
    now: datetime | None = None,
) -> None:
    """Add `amount` to today's counter. Without ids: the logged-in user and
    their organization (or `organization_id`, e.g. an API key's)."""
    from app.admin.models import UsageDaily

    if metric not in METRICS or amount <= 0:
        return
    user = _current_user()
    if user is not None and getattr(user, "is_superadmin", lambda: False)():
        return
    if user_id is None and user is not None:
        user_id = user.id
    if organization_id is None and user is not None:
        organization_id = user.organization_id
    if organization_id is None:
        return
    day = (now or datetime.now(timezone.utc)).date()
    filters = dict(organization_id=organization_id, user_id=user_id, day=day, metric=metric)
    try:
        for _attempt in range(2):
            try:
                with db.session.begin_nested():
                    updated = UsageDaily.query.filter_by(**filters).update(
                        {UsageDaily.count: UsageDaily.count + amount}, synchronize_session=False
                    )
                    if not updated:
                        db.session.add(UsageDaily(count=amount, **filters))
                        db.session.flush()
                break
            except IntegrityError:
                continue  # another request created the row: add to it
        if commit:
            db.session.commit()
    except SQLAlchemyError:
        logger.exception("Usage tracking failed: %s", metric)
        db.session.rollback()
