"""Kuukausiraportti: what the customer got out of FlowLeads in one month.

Counted per calendar month in Finnish time, with the previous month beside it:
targets received, moved to leads and hidden, new leads, calls and emails,
proposals sent and accepted, deals won and lost (by when the stage changed,
not when the lead was last edited). The page is /reports/monthly; the
scheduler job "monthly_report" emails last month's report to the
organization's admins on the first day of the month.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.extensions import db

HELSINKI = ZoneInfo("Europe/Helsinki")
MONTHS_FI = (
    "tammikuu", "helmikuu", "maaliskuu", "huhtikuu", "toukokuu", "kesäkuu",
    "heinäkuu", "elokuu", "syyskuu", "lokakuu", "marraskuu", "joulukuu",
)


@dataclass(frozen=True)
class Month:
    year: int
    month: int

    @classmethod
    def parse(cls, value: str | None, *, today: date | None = None) -> "Month":
        """'2026-10' -> Month(2026, 10); anything else -> the current month."""
        today = today or datetime.now(HELSINKI).date()
        try:
            year, month = (int(part) for part in (value or "").split("-", 1))
            if 2000 <= year <= 2100 and 1 <= month <= 12:
                return cls(year, month)
        except ValueError:
            pass
        return cls(today.year, today.month)

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        return f"{MONTHS_FI[self.month - 1].capitalize()} {self.year}"

    def previous(self) -> "Month":
        return Month(self.year - 1, 12) if self.month == 1 else Month(self.year, self.month - 1)

    def next(self) -> "Month":
        return Month(self.year + 1, 1) if self.month == 12 else Month(self.year, self.month + 1)

    def bounds(self) -> tuple[datetime, datetime]:
        """[start, end) in UTC of the month in Finnish time."""
        start = datetime(self.year, self.month, 1, tzinfo=HELSINKI)
        following = self.next()
        end = datetime(following.year, following.month, 1, tzinfo=HELSINKI)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _count(query) -> int:
    return int(query.scalar() or 0)


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def month_figures(organization_id: int, month: Month) -> dict:
    from app.leads.models import Activity, Lead
    from app.listings.models import ListingMatch
    from app.listings.target_services import fetch_enabled
    from app.proposals.models import Proposal

    start, end = month.bounds()

    def between(column):
        return (column >= start) & (column < end)

    received_at = ListingMatch.released_at if fetch_enabled() else ListingMatch.created_at
    received = _count(
        db.session.query(func.count(func.distinct(ListingMatch.listing_id))).filter(
            ListingMatch.organization_id == organization_id, between(received_at)
        )
    )
    transferred = _count(
        db.session.query(func.count(func.distinct(ListingMatch.lead_id))).filter(
            ListingMatch.organization_id == organization_id,
            ListingMatch.status == "lead_created",
            between(ListingMatch.lead_created_at),
        )
    )
    hidden = _count(
        db.session.query(func.count(func.distinct(ListingMatch.listing_id))).filter(
            ListingMatch.organization_id == organization_id,
            ListingMatch.status == "dismissed",
            between(ListingMatch.dismissed_at),
        )
    )
    new_leads = _count(
        db.session.query(func.count(Lead.id)).filter(Lead.organization_id == organization_id, between(Lead.created_at))
    )

    def activities(kind: str) -> int:
        return _count(
            db.session.query(func.count(Activity.id)).filter(
                Activity.organization_id == organization_id, Activity.type == kind, between(Activity.created_at)
            )
        )

    def proposals(column):
        row = (
            db.session.query(func.count(Proposal.id), func.coalesce(func.sum(Proposal.total), 0))
            .filter(Proposal.organization_id == organization_id, between(column))
            .one()
        )
        return int(row[0] or 0), _money(row[1])

    sent, sent_value = proposals(Proposal.sent_at)
    accepted, accepted_value = proposals(Proposal.accepted_at)

    def closed(status: str):
        row = (
            db.session.query(func.count(Lead.id), func.coalesce(func.sum(Lead.deal_value), 0))
            .filter(
                Lead.organization_id == organization_id,
                Lead.status == status,
                between(func.coalesce(Lead.stage_changed_at, Lead.updated_at)),
            )
            .one()
        )
        return int(row[0] or 0), _money(row[1])

    won, won_value = closed("won")
    lost, _lost_value = closed("lost")
    return {
        "received": received,
        "transferred": transferred,
        "hidden": hidden,
        "new_leads": new_leads,
        "calls": activities("call"),
        "emails": activities("email_sent"),
        "proposals_sent": sent,
        "proposals_sent_value": sent_value,
        "proposals_accepted": accepted,
        "proposals_accepted_value": accepted_value,
        "won": won,
        "won_value": won_value,
        "lost": lost,
        "transfer_rate": round(100 * transferred / received) if received else None,
        "win_rate": round(100 * won / (won + lost)) if (won + lost) else None,
    }


ROWS = (
    ("received", "Kohteita saatu", "count"),
    ("transferred", "Siirretty liideiksi", "count"),
    ("hidden", "Piilotettu", "count"),
    ("new_leads", "Uusia liidejä (kaikki lähteet)", "count"),
    ("calls", "Soittoja kirjattu", "count"),
    ("emails", "Sähköposteja lähetetty", "count"),
    ("proposals_sent", "Tarjouksia lähetetty", "count"),
    ("proposals_sent_value", "Lähetettyjen tarjousten arvo", "money"),
    ("proposals_accepted", "Tarjouksia hyväksytty", "count"),
    ("proposals_accepted_value", "Hyväksyttyjen tarjousten arvo", "money"),
    ("won", "Kauppoja voitettu", "count"),
    ("won_value", "Voitettujen kauppojen arvo", "money"),
    ("lost", "Kauppoja hävitty", "count"),
)


def monthly_report(organization_id: int, month: Month) -> dict:
    current = month_figures(organization_id, month)
    previous = month_figures(organization_id, month.previous())
    rows = []
    for key, label, kind in ROWS:
        value, before = current[key], previous[key]
        rows.append({"key": key, "label": label, "kind": kind, "value": value, "previous": before, "change": value - before})
    return {
        "month": month,
        "figures": current,
        "previous_figures": previous,
        "rows": rows,
        "people": _per_user(organization_id, month),
        "won_deals": _won_deals(organization_id, month),
        "has_activity": any(current[key] for key, _label, _kind in ROWS),
    }


def _per_user(organization_id: int, month: Month) -> list[dict]:
    from app.leads.models import Activity, Lead
    from app.users.models import User

    start, end = month.bounds()
    people: dict[int, dict] = {}
    rows = (
        db.session.query(Activity.user_id, Activity.type, func.count(Activity.id))
        .filter(
            Activity.organization_id == organization_id,
            Activity.user_id.isnot(None),
            Activity.type.in_(("call", "email_sent")),
            Activity.created_at >= start,
            Activity.created_at < end,
        )
        .group_by(Activity.user_id, Activity.type)
        .all()
    )
    for user_id, kind, count in rows:
        people.setdefault(user_id, {"calls": 0, "emails": 0, "won": 0})["calls" if kind == "call" else "emails"] = count
    won = (
        db.session.query(Lead.assigned_to, func.count(Lead.id))
        .filter(
            Lead.organization_id == organization_id,
            Lead.status == "won",
            Lead.assigned_to.isnot(None),
            func.coalesce(Lead.stage_changed_at, Lead.updated_at) >= start,
            func.coalesce(Lead.stage_changed_at, Lead.updated_at) < end,
        )
        .group_by(Lead.assigned_to)
        .all()
    )
    for user_id, count in won:
        people.setdefault(user_id, {"calls": 0, "emails": 0, "won": 0})["won"] = count
    if not people:
        return []
    users = {u.id: u for u in User.query.filter(User.id.in_(list(people)), User.organization_id == organization_id).all()}
    result = [{"email": users[uid].email, **stats} for uid, stats in people.items() if uid in users]
    return sorted(result, key=lambda r: (-r["won"], -r["calls"], r["email"]))


def _won_deals(organization_id: int, month: Month, limit: int = 10) -> list:
    from app.leads.models import Lead

    start, end = month.bounds()
    changed = func.coalesce(Lead.stage_changed_at, Lead.updated_at)
    return (
        Lead.query.filter(
            Lead.organization_id == organization_id, Lead.status == "won", changed >= start, changed < end
        )
        .order_by(Lead.deal_value.desc().nullslast(), Lead.id.asc())
        .limit(limit)
        .all()
    )


def format_money(value) -> str:
    """1234.5 -> '1 234,50 €' (Finnish)."""
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    whole, cents = f"{amount:.2f}".split(".")
    sign = "-" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return f"{sign}{' '.join(groups) or '0'},{cents} €"


# --- email ---------------------------------------------------------------------------


def send_monthly_reports(*, now: datetime | None = None) -> int:
    """Email last month's report to each active organization's admins, once a
    month (OrganizationSettings.monthly_report_sent_for). Organizations with no
    activity at all, or with the report switched off, get nothing."""
    from flask import current_app

    from app.tasks.settings import get_organization_settings
    from app.users.models import Organization, User

    now = now or datetime.now(timezone.utc)
    local = now.astimezone(HELSINKI)
    month = Month(local.year, local.month).previous()
    sent = 0
    for organization in Organization.query.filter_by(is_active=True).all():
        settings = get_organization_settings(organization.id)
        if not settings.monthly_report_enabled or settings.monthly_report_sent_for == month.key:
            continue
        report = monthly_report(organization.id, month)
        settings.monthly_report_sent_for = month.key
        db.session.commit()
        if not report["has_activity"]:
            continue
        admins = User.query.filter(
            User.organization_id == organization.id, User.is_active.is_(True), User.role == "admin"
        ).all()
        recipients = [u.email for u in admins if u.email]
        if not recipients:
            continue
        try:
            _email_report(current_app, organization, report, recipients)
            sent += 1
        except Exception:  # one organization's mail problem must not stop the rest
            current_app.logger.exception("Monthly report email failed for org %s", organization.id)
    return sent


def _email_report(app, organization, report: dict, recipients: list[str]) -> None:
    from flask_mail import Message

    from app.email.services import send_system_message

    base_url = (app.config.get("APP_BASE_URL") or "").rstrip("/")
    link = f"{base_url}/reports/monthly?month={report['month'].key}"
    # rendered without the request-bound context processors: the scheduler
    # sends this with no request and no signed-in user
    html = app.jinja_env.get_template("analytics/monthly_email.html").render(
        report=report, organization=organization, link=link, format_money=format_money
    )
    lines = [f"{row['label']}: {format_money(row['value']) if row['kind'] == 'money' else row['value']}" for row in report["rows"]]
    send_system_message(
        Message(
            subject=f"FlowLeads: kuukausiraportti {report['month'].label.lower()} ({organization.name})",
            recipients=recipients,
            html=html,
            body="\n".join([f"Kuukausiraportti {report['month'].label}", "", *lines, "", f"Koko raportti: {link}"]),
        )
    )
