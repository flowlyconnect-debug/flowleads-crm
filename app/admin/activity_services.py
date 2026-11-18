"""Asiakasseuranta (superadmin): what each customer does with the targets,
to spot one that collects contacts to resell them instead of selling to them.

Sources: daily counters (`UsageDaily`, `app/core/usage.py`), logins in the
audit log (with IP) and the customer's leads and their activities. Every
signal below is a reason to look, not a verdict: the page shows the numbers
behind each one."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, func, or_

from app.admin.models import UsageDaily
from app.core.usage import EXPORT_METRICS, METRICS, VIEW_METRICS
from app.extensions import db

# Thresholds (per period unless said otherwise)
EXPORT_ROWS = 50  # rows taken out (CSV, CRM file or push, API)
EXPORT_SHARE = 0.5  # ...or at least this share of the transferred leads, from 20 rows
UNWORKED_MIN = 20  # transferred leads older than UNWORKED_AFTER_DAYS...
UNWORKED_SHARE = 0.8  # ...of which this share has had no work at all
UNWORKED_AFTER_DAYS = 7
DAY_VIEWS = 300  # contact-data page views by one customer in one day
VIEWS_PER_TRANSFER = 40  # many views, hardly any transfers (browsing contacts without using FlowLeads)
VIEWS_MIN = 500
IPS_PER_USER = 3  # distinct login addresses per user (a shared account)
IPS_MIN = 6
DAY_TRANSFERS = 50  # targets moved to leads by one customer in one day

CATALOGUE_SOURCES = ("oikotie", "import")
# activity types that are no work by the customer (created, edited, archived, automatic)
NO_WORK_TYPES = ("created", "updated", "archived")


@dataclass
class Signal:
    key: str
    title: str
    detail: str


def _since(days: int, now: datetime) -> date:
    return (now - timedelta(days=days)).date()


def _usage(since: date, organization_id: int | None = None):
    query = db.session.query(UsageDaily.organization_id, UsageDaily.metric, func.sum(UsageDaily.count)).filter(
        UsageDaily.day >= since
    )
    if organization_id is not None:
        query = query.filter(UsageDaily.organization_id == organization_id)
    totals: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for org_id, metric, total in query.group_by(UsageDaily.organization_id, UsageDaily.metric):
        totals[org_id][metric] = int(total or 0)
    return totals


def _daily_peaks(since: date, organization_id: int | None = None) -> dict[int, dict[str, tuple[int, date | None]]]:
    """Per organization: the busiest day of views and of transfers."""
    query = db.session.query(UsageDaily.organization_id, UsageDaily.day, UsageDaily.metric, func.sum(UsageDaily.count)).filter(
        UsageDaily.day >= since, UsageDaily.metric.in_(VIEW_METRICS + ("targets_transferred",))
    )
    if organization_id is not None:
        query = query.filter(UsageDaily.organization_id == organization_id)
    per_day: dict[tuple[int, date], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for org_id, day, metric, total in query.group_by(UsageDaily.organization_id, UsageDaily.day, UsageDaily.metric):
        kind = "transfers" if metric == "targets_transferred" else "views"
        per_day[(org_id, day)][kind] += int(total or 0)
    peaks: dict[int, dict[str, tuple[int, date | None]]] = defaultdict(lambda: {"views": (0, None), "transfers": (0, None)})
    for (org_id, day), kinds in per_day.items():
        for kind, total in kinds.items():
            if total > peaks[org_id][kind][0]:
                peaks[org_id][kind] = (total, day)
    return peaks


def _logins(since_dt: datetime, organization_id: int | None = None) -> dict[int, dict]:
    from app.users.models import AuditLog

    query = db.session.query(
        AuditLog.organization_id,
        func.count(AuditLog.id),
        func.count(func.distinct(AuditLog.ip_address)),
        func.count(func.distinct(AuditLog.user_id)),
    ).filter(AuditLog.action == "login_success", AuditLog.created_at >= since_dt, AuditLog.organization_id.isnot(None))
    if organization_id is not None:
        query = query.filter(AuditLog.organization_id == organization_id)
    return {
        org_id: {"logins": logins, "ips": ips, "login_users": users}
        for org_id, logins, ips, users in query.group_by(AuditLog.organization_id)
    }


def _worked_filter():
    """Leads someone of the customer has worked on: a call, note, e-mail,
    stage change, task or meeting, or a contact time."""
    from app.leads.models import Activity, Lead

    work = (
        db.session.query(Activity.id)
        .filter(
            Activity.lead_id == Lead.id,
            or_(
                and_(Activity.user_id.isnot(None), Activity.type.notin_(NO_WORK_TYPES)),
                Activity.type.in_(("email_sent", "email_reply_received", "call")),
            ),
        )
        .exists()
    )
    return or_(work, Lead.last_contacted_at.isnot(None))


def _unworked(since_dt: datetime, now: datetime, organization_id: int | None = None) -> dict[int, dict]:
    """Catalogue leads created in the period and older than a week: how many,
    and how many nobody has worked on."""
    from app.leads.models import Lead

    cutoff = now - timedelta(days=UNWORKED_AFTER_DAYS)
    base = db.session.query(Lead.organization_id, func.count(Lead.id)).filter(
        Lead.source.in_(CATALOGUE_SOURCES), Lead.created_at >= since_dt, Lead.created_at <= cutoff
    )
    if organization_id is not None:
        base = base.filter(Lead.organization_id == organization_id)
    total = dict(base.group_by(Lead.organization_id).all())
    worked = dict(base.filter(_worked_filter()).group_by(Lead.organization_id).all())
    return {org_id: {"old_leads": count, "unworked": count - worked.get(org_id, 0)} for org_id, count in total.items()}


def _signals(row: dict) -> list[Signal]:
    signals: list[Signal] = []
    exported, transferred = row["exported"], row["transferred"]
    if exported >= EXPORT_ROWS or (exported >= 20 and transferred and exported >= EXPORT_SHARE * transferred):
        signals.append(Signal(
            "export", "Vie liidejä ulos",
            f"{exported} liidiä viety CSV:nä, CRM:ään tai API:lla ({transferred} siirretty liideiksi).",
        ))
    old, unworked = row["old_leads"], row["unworked"]
    if old >= UNWORKED_MIN and unworked >= UNWORKED_SHARE * old:
        signals.append(Signal(
            "unworked", "Siirtää mutta ei työstä",
            f"{unworked}/{old} yli viikon vanhaa kohdeliidiä ilman yhtään soittoa, muistiinpanoa, viestiä tai vaiheen muutosta.",
        ))
    peak_views, peak_day = row["peak_views"]
    if peak_views >= DAY_VIEWS:
        signals.append(Signal(
            "views_day", "Selaa paljon yhteystietoja",
            f"{peak_views} kohde-, taloyhtiö- tai isännöitsijäsivun avausta yhtenä päivänä ({peak_day:%d.%m.%Y}).",
        ))
    elif row["views"] >= VIEWS_MIN and row["views"] >= VIEWS_PER_TRANSFER * max(transferred, 1):
        signals.append(Signal(
            "views_ratio", "Selaa mutta ei siirrä",
            f"{row['views']} sivunavausta, vain {transferred} siirtoa liideiksi.",
        ))
    if row["ips"] >= IPS_MIN and row["ips"] >= IPS_PER_USER * max(row["login_users"], 1):
        signals.append(Signal(
            "ips", "Tunnuksia käytetään monesta paikasta",
            f"{row['ips']} eri IP-osoitetta {row['login_users']} käyttäjällä ({row['logins']} kirjautumista).",
        ))
    peak_transfers, transfer_day = row["peak_transfers"]
    if peak_transfers >= DAY_TRANSFERS:
        signals.append(Signal(
            "bulk", "Massasiirto",
            f"{peak_transfers} kohdetta siirretty liideiksi yhtenä päivänä ({transfer_day:%d.%m.%Y}).",
        ))
    return signals


def _level(signals: list[Signal]) -> tuple[str, str]:
    if len(signals) >= 2:
        return "check", "Tarkista"
    if signals:
        return "watch", "Seuraa"
    return "ok", "Normaali"


def _row(org, usage: dict, peaks: dict, logins: dict, unworked: dict, users: dict) -> dict:
    row = {
        "org": org,
        "users": users.get(org.id, (0, None))[0],
        "last_login": users.get(org.id, (0, None))[1],
        "views": sum(usage.get(m, 0) for m in VIEW_METRICS),
        "transferred": usage.get("targets_transferred", 0),
        "exported": sum(usage.get(m, 0) for m in EXPORT_METRICS),
        "usage": dict(usage),
        "peak_views": peaks.get("views", (0, None)),
        "peak_transfers": peaks.get("transfers", (0, None)),
        "logins": logins.get("logins", 0),
        "ips": logins.get("ips", 0),
        "login_users": logins.get("login_users", 0),
        "old_leads": unworked.get("old_leads", 0),
        "unworked": unworked.get("unworked", 0),
    }
    row["signals"] = _signals(row)
    row["level"], row["level_label"] = _level(row["signals"])
    return row


def _users(organization_id: int | None = None) -> dict[int, tuple[int, datetime | None]]:
    from app.users.models import User

    query = db.session.query(User.organization_id, func.count(User.id), func.max(User.last_login_at)).filter(
        User.is_active.is_(True), User.organization_id.isnot(None), User.role != "superadmin"
    )
    if organization_id is not None:
        query = query.filter(User.organization_id == organization_id)
    return {org_id: (count, last) for org_id, count, last in query.group_by(User.organization_id)}


LEVEL_ORDER = {"check": 0, "watch": 1, "ok": 2}


def customer_activity(*, days: int = 30, now: datetime | None = None) -> list[dict]:
    """One row per customer organization, the ones to check first."""
    from app.users.models import Organization

    now = now or datetime.now(timezone.utc)
    since = _since(days, now)
    since_dt = now - timedelta(days=days)
    usage, peaks = _usage(since), _daily_peaks(since)
    logins, unworked, users = _logins(since_dt), _unworked(since_dt, now), _users()
    rows = [
        _row(org, usage.get(org.id, {}), peaks.get(org.id, {}), logins.get(org.id, {}), unworked.get(org.id, {}), users)
        for org in Organization.query.order_by(Organization.name).all()
    ]
    rows.sort(key=lambda r: (LEVEL_ORDER[r["level"]], -len(r["signals"]), -r["exported"], -r["views"], r["org"].name.lower()))
    return rows


def customer_activity_detail(organization_id: int, *, days: int = 30, now: datetime | None = None) -> dict | None:
    """One customer: the summary row, each user's numbers, day by day and the latest events."""
    from app.users.models import AuditLog, Organization, User

    organization = db.session.get(Organization, organization_id)
    if organization is None:
        return None
    now = now or datetime.now(timezone.utc)
    since = _since(days, now)
    since_dt = now - timedelta(days=days)
    row = _row(
        organization,
        _usage(since, organization_id).get(organization_id, {}),
        _daily_peaks(since, organization_id).get(organization_id, {}),
        _logins(since_dt, organization_id).get(organization_id, {}),
        _unworked(since_dt, now, organization_id).get(organization_id, {}),
        _users(organization_id),
    )

    per_user: dict[int | None, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_day: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for user_id, day, metric, total in (
        db.session.query(UsageDaily.user_id, UsageDaily.day, UsageDaily.metric, func.sum(UsageDaily.count))
        .filter(UsageDaily.organization_id == organization_id, UsageDaily.day >= since)
        .group_by(UsageDaily.user_id, UsageDaily.day, UsageDaily.metric)
    ):
        per_user[user_id][metric] += int(total or 0)
        per_day[day][metric] += int(total or 0)

    ips = dict(
        db.session.query(AuditLog.user_id, func.count(func.distinct(AuditLog.ip_address)))
        .filter(AuditLog.organization_id == organization_id, AuditLog.action == "login_success", AuditLog.created_at >= since_dt)
        .group_by(AuditLog.user_id)
        .all()
    )
    logins = dict(
        db.session.query(AuditLog.user_id, func.count(AuditLog.id))
        .filter(AuditLog.organization_id == organization_id, AuditLog.action == "login_success", AuditLog.created_at >= since_dt)
        .group_by(AuditLog.user_id)
        .all()
    )

    def summary(metrics: dict) -> dict:
        return {
            "views": sum(metrics.get(m, 0) for m in VIEW_METRICS),
            "transferred": metrics.get("targets_transferred", 0),
            "exported": sum(metrics.get(m, 0) for m in EXPORT_METRICS),
        }

    users = []
    for user in User.query.filter_by(organization_id=organization_id).order_by(User.email).all():
        users.append({"user": user, "logins": logins.get(user.id, 0), "ips": ips.get(user.id, 0), **summary(per_user.get(user.id, {}))})
    if None in per_user:  # API keys
        users.append({"user": None, "logins": 0, "ips": 0, **summary(per_user[None])})

    days_rows = [
        {"day": day, **summary(metrics), "metrics": dict(metrics)}
        for day, metrics in sorted(per_day.items(), reverse=True)
    ]
    watched = (
        "login_success", "login_failed", "leads_exported", "crm_export", "crm_file_exported", "targets_transferred",
        "gdpr_data_exported", "leads_imported", "crm_connected", "export_blocked",
    )
    events = (
        AuditLog.query.filter(AuditLog.organization_id == organization_id, AuditLog.action.in_(watched))
        .order_by(AuditLog.created_at.desc())
        .limit(60)
        .all()
    )
    return {"row": row, "users": users, "days": days_rows, "events": events, "metrics": METRICS, "since": since}


EVENT_LABELS = {
    "login_success": "Kirjautui",
    "login_failed": "Epäonnistunut kirjautuminen",
    "leads_exported": "Vei liidejä CSV:nä",
    "crm_export": "Vei liidejä CRM:ään",
    "crm_file_exported": "Latasi CRM-tuontitiedoston",
    "targets_transferred": "Siirsi kohteita liideiksi",
    "gdpr_data_exported": "Latasi organisaation tiedot",
    "leads_imported": "Toi liidejä",
    "crm_connected": "Yhdisti CRM:n",
    "export_blocked": "Vienti estettiin (raja tai rooli)",
}


def event_detail(event) -> str:
    meta = event.metadata_json or {}
    if "rows" in meta:
        return f"{meta['rows']} riviä" + (f" ({meta['provider']})" if meta.get("provider") else "")
    if event.action == "targets_transferred":
        return f"{meta.get('created', 0)} uutta liidiä, {meta.get('merged', 0)} yhdistetty"
    if event.action == "crm_export":
        return f"{meta.get('provider', '')}: {meta.get('created', 0)} uutta, {meta.get('updated', 0)} päivitetty"
    return ""


# ---------------------------------------------------------------------------
# Daily alert to superadmins (scheduler job `activity_alerts`)
# ---------------------------------------------------------------------------

ALERT_REPEAT_DAYS = 14  # the same signals of a customer are mailed again after this long


def _last_alert_signals(organization_id: int, since: datetime) -> set[str] | None:
    from app.users.models import AuditLog

    last = (
        AuditLog.query.filter(
            AuditLog.organization_id == organization_id,
            AuditLog.action == "activity_alert_sent",
            AuditLog.created_at >= since,
        )
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    return set((last.metadata_json or {}).get("signals") or []) if last is not None else None


def send_activity_alerts(*, now: datetime | None = None) -> int:
    """Mail every active superadmin about customers that turned "Tarkista"
    (two or more signals over the last 30 days). A customer is mailed again
    only when its signals change or after ALERT_REPEAT_DAYS. Returns the
    customers in the e-mail."""
    import html

    from flask import current_app, url_for

    from app.core.audit import log_audit
    from app.email.services import _mailgun_send, email_sending_enabled
    from app.users.models import User

    if not current_app.config.get("ACTIVITY_ALERTS_ENABLED", True) or not email_sending_enabled():
        return 0
    now = now or datetime.now(timezone.utc)
    flagged = []
    for row in customer_activity(days=30, now=now):
        if row["level"] != "check" or not row["org"].is_active:
            continue
        keys = {s.key for s in row["signals"]}
        if _last_alert_signals(row["org"].id, now - timedelta(days=ALERT_REPEAT_DAYS)) == keys:
            continue
        flagged.append((row, keys))
    if not flagged:
        return 0
    recipients = [u.email for u in User.query.filter_by(role="superadmin", is_active=True).all() if u.email]
    if not recipients:
        return 0
    base = (current_app.config.get("APP_BASE_URL") or "").rstrip("/")
    items_html, items_text = [], []
    for row, _keys in flagged:
        link = f"{base}/admin/activity/{row['org'].id}" if base else f"/admin/activity/{row['org'].id}"
        reasons = "".join(f"<li>{html.escape(s.title)}: {html.escape(s.detail)}</li>" for s in row["signals"])
        items_html.append(f"<h3 style='margin:16px 0 4px;'>{html.escape(row['org'].name)}</h3><ul>{reasons}</ul>"
                          f"<p><a href='{html.escape(link)}'>Avaa asiakasseuranta</a></p>")
        items_text.append(row["org"].name + "\n" + "\n".join(f"- {s.title}: {s.detail}" for s in row["signals"]) + f"\n{link}")
    count = len(flagged)
    subject = f"FlowLeads: {count} {'asiakas' if count == 1 else 'asiakasta'} tarkistettavana asiakasseurannassa"
    body_html = (
        "<div style='font-family:Arial,sans-serif;font-size:15px;line-height:1.5;color:#172033;max-width:640px;'>"
        "<p>Näillä asiakkailla on asiakasseurannassa kaksi tai useampi merkki viimeisen 30 päivän ajalta. "
        "Merkit ovat syitä katsoa tarkemmin, eivät tuomioita.</p>"
        + "".join(items_html)
        + "</div>"
    )
    body_text = "Tarkistettavat asiakkaat (30 pv):\n\n" + "\n\n".join(items_text)
    from_name = current_app.config.get("MAILGUN_FROM_NAME") or "FlowLeads"
    from_email = current_app.config.get("MAILGUN_FROM_EMAIL") or ""
    sent = False
    for email in recipients:
        ok, _id, _error = _mailgun_send(
            to_email=email, subject=subject, body_html=body_html, body_text=body_text,
            from_name=from_name, from_email=from_email,
        )
        sent = sent or ok
    if not sent:
        return 0
    for row, keys in flagged:
        log_audit(
            "activity_alert_sent",
            organization_id=row["org"].id,
            target_type="organization",
            target_id=row["org"].id,
            metadata={"signals": sorted(keys)},
        )
    db.session.commit()
    return count
