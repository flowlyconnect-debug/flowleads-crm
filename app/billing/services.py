"""Trial, monthly contract and cancellation.

States of Organization.subscription_status:
    trial      free until trial_ends_at, everything works; afterwards the
               organization keeps its data but cannot fetch or move new targets
               until it orders a contract;
    active     monthly contract (or an older customer billed by agreement);
    cancelled  works until cancel_at (end of the month it was cancelled in);
    expired    a cancelled contract that has ended.

Billing is manual: ordering and cancelling email the platform's superadmins,
who invoice. Nothing here charges a card.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.billing.plans import PLANS_BY_KEY, TRIAL_DAYS, TRIAL_PLAN
from app.extensions import db

HELSINKI = ZoneInfo("Europe/Helsinki")
STATUS_LABELS = {
    "trial": "Kokeilujakso",
    "trial_expired": "Kokeilujakso päättynyt",
    "active": "Kuukausisopimus",
    "cancelled": "Irtisanottu",
    "expired": "Päättynyt",
}


class BillingError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass(frozen=True)
class SubscriptionState:
    status: str  # trial / trial_expired / active / cancelled / expired
    plan: str | None
    can_use: bool
    ends_at: datetime | None
    days_left: int | None

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def last_day(self) -> str:
        """The last day of use, Finnish date ('31.12.2026')."""
        if self.ends_at is None:
            return ""
        return (self.ends_at - timedelta(seconds=1)).astimezone(HELSINKI).strftime("%d.%m.%Y")

    @property
    def plan_name(self) -> str:
        plan = PLANS_BY_KEY.get(self.plan or "")
        return plan["name"] if plan else "Sopimuksen mukaan"


def subscription_state(organization, *, now: datetime | None = None) -> SubscriptionState:
    now = now or _now()
    status = organization.subscription_status or "active"
    if status == "trial":
        ends = _aware(organization.trial_ends_at)
        if ends is None or ends > now:
            days = max(0, (ends - now).days + (1 if (ends - now).seconds else 0)) if ends else None
            return SubscriptionState("trial", organization.plan or TRIAL_PLAN, True, ends, days)
        return SubscriptionState("trial_expired", organization.plan, False, ends, 0)
    if status == "cancelled":
        ends = _aware(organization.cancel_at)
        if ends is None or ends > now:
            days = (ends - now).days if ends else None
            return SubscriptionState("cancelled", organization.plan, True, ends, days)
        return SubscriptionState("expired", organization.plan, False, ends, 0)
    if status == "expired":
        return SubscriptionState("expired", organization.plan, False, _aware(organization.cancel_at), 0)
    return SubscriptionState("active", organization.plan, True, None, None)


def organization_can_use(organization_id: int | None) -> bool:
    """False only when a trial or contract has ended. Unknown organizations
    (superadmin views without one) are never blocked here."""
    from app.users.models import Organization

    if not organization_id:
        return True
    organization = db.session.get(Organization, organization_id)
    return organization is None or subscription_state(organization).can_use


def start_trial(organization, *, now: datetime | None = None, days: int = TRIAL_DAYS) -> None:
    now = now or _now()
    organization.subscription_status = "trial"
    organization.plan = TRIAL_PLAN
    organization.trial_ends_at = now + timedelta(days=days)
    organization.trial_reminder_sent_at = None
    organization.cancel_at = None


def end_of_month(now: datetime) -> datetime:
    """Midnight after the last day of the month, Finnish time, as UTC."""
    local = now.astimezone(HELSINKI)
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return datetime(year, month, 1, tzinfo=HELSINKI).astimezone(timezone.utc)


def subscribe(organization, plan_key: str, *, actor, now: datetime | None = None) -> None:
    """The customer orders the monthly contract (from the Tilaus page)."""
    plan = PLANS_BY_KEY.get(plan_key)
    if plan is None or plan.get("price") is None:
        raise BillingError("Choose a plan with a monthly price.")
    now = now or _now()
    state = subscription_state(organization, now=now)
    if state.status == "active" and organization.plan == plan_key:
        raise BillingError("This plan is already in use.")
    organization.subscription_status = "active"
    organization.plan = plan_key
    organization.cancel_at = None
    if state.status != "active" or organization.subscription_started_at is None:
        organization.subscription_started_at = now
    _notify_superadmins(
        organization,
        f"Uusi tilaus: {organization.name} – {plan['name']} {plan['price']} € / kk",
        f"{organization.name} tilasi kuukausisopimuksen ({plan['name']}, {plan['price']} € / kk alv 0 %).\n"
        f"Tilaaja: {actor.email}\nAlkaa: {now.astimezone(HELSINKI).strftime('%d.%m.%Y')}\n\n"
        "Lähetä ensimmäinen lasku.",
    )


def cancel(organization, *, actor, now: datetime | None = None) -> datetime:
    """Cancel the monthly contract: in use until the end of this month."""
    now = now or _now()
    state = subscription_state(organization, now=now)
    if state.status != "active":
        raise BillingError("Only an active monthly contract can be cancelled.")
    organization.subscription_status = "cancelled"
    organization.cancel_at = end_of_month(now)
    _notify_superadmins(
        organization,
        f"Irtisanominen: {organization.name}",
        f"{organization.name} irtisanoi sopimuksen. Palvelu on käytössä "
        f"{(organization.cancel_at - timedelta(seconds=1)).astimezone(HELSINKI).strftime('%d.%m.%Y')} asti.\n"
        f"Irtisanoja: {actor.email}\n\nÄlä laskuta seuraavaa kuukautta.",
    )
    return organization.cancel_at


def resume(organization, *, actor, now: datetime | None = None) -> None:
    """Undo a cancellation before it takes effect."""
    now = now or _now()
    if subscription_state(organization, now=now).status != "cancelled":
        raise BillingError("Only a cancelled contract that is still running can be resumed.")
    organization.subscription_status = "active"
    organization.cancel_at = None
    _notify_superadmins(
        organization,
        f"Irtisanominen peruttu: {organization.name}",
        f"{organization.name} perui irtisanomisen ({actor.email}). Laskutus jatkuu normaalisti.",
    )


def admin_set(organization, action: str, *, plan_key: str | None = None, now: datetime | None = None) -> str:
    """Superadmin controls on the customer list. Returns a Finnish summary."""
    now = now or _now()
    if action == "extend_trial":
        base = max(_aware(organization.trial_ends_at) or now, now) if organization.subscription_status == "trial" else now
        organization.subscription_status = "trial"
        organization.plan = organization.plan or TRIAL_PLAN
        organization.trial_ends_at = base + timedelta(days=TRIAL_DAYS)
        organization.trial_reminder_sent_at = None
        return f"Kokeilujaksoa jatkettu {organization.trial_ends_at.astimezone(HELSINKI).strftime('%d.%m.%Y')} asti."
    if action == "activate":
        if plan_key and plan_key not in PLANS_BY_KEY:
            raise BillingError("Unknown plan.")
        organization.subscription_status = "active"
        organization.plan = plan_key or organization.plan
        organization.cancel_at = None
        organization.subscription_started_at = organization.subscription_started_at or now
        return "Kuukausisopimus merkitty aktiiviseksi."
    if action == "end":
        organization.subscription_status = "expired"
        organization.cancel_at = now
        return "Sopimus päätetty. Asiakas näkee tietonsa, mutta ei saa uusia kohteita."
    raise BillingError("Unknown action.")


def _notify_superadmins(organization, subject: str, body: str) -> None:
    from flask import current_app
    from flask_mail import Message

    from app.email.services import send_system_message
    from app.users.models import User

    recipients = [u.email for u in User.query.filter_by(role="superadmin", is_active=True).all() if u.email]
    if not recipients:
        return
    try:
        send_system_message(Message(subject=f"FlowLeads: {subject}", recipients=recipients, body=body))
    except Exception:  # the order itself must not fail on a mail problem
        current_app.logger.exception("Billing notification failed for org %s", organization.id)


def send_trial_reminders(*, now: datetime | None = None) -> int:
    """Three days before a trial ends: an in-app notification and an email to
    the organization's admins, once per trial."""
    from flask import current_app
    from flask_mail import Message

    from app.email.services import send_system_message
    from app.notifications.services import NotificationService
    from app.users.models import Organization, User

    now = now or _now()
    soon = now + timedelta(days=3)
    reminded = 0
    trials = Organization.query.filter(
        Organization.is_active.is_(True),
        Organization.subscription_status == "trial",
        Organization.trial_ends_at.isnot(None),
        Organization.trial_reminder_sent_at.is_(None),
    ).all()
    base_url = (current_app.config.get("APP_BASE_URL") or "").rstrip("/")
    for organization in trials:
        ends = _aware(organization.trial_ends_at)
        if ends > soon or ends < now:
            continue
        admins = User.query.filter_by(organization_id=organization.id, role="admin", is_active=True).all()
        day = (ends - timedelta(seconds=1)).astimezone(HELSINKI).strftime("%d.%m.%Y")
        message = (
            f"Kokeilujaksosi päättyy {day}. Jatka käyttöä valitsemalla kuukausisopimus Tilaus-sivulta: "
            "ei määräaikaa, irtisanottavissa milloin tahansa."
        )
        for admin in admins:
            NotificationService.create(
                user_id=admin.id,
                organization_id=organization.id,
                type="trial_ending",
                title="Kokeilujakso päättyy pian",
                message=message,
                link="/billing",
            )
        organization.trial_reminder_sent_at = now
        db.session.commit()
        recipients = [a.email for a in admins if a.email]
        if recipients:
            try:
                send_system_message(
                    Message(
                        subject="FlowLeads: kokeilujaksosi päättyy pian",
                        recipients=recipients,
                        body=f"{message}\n\n{base_url}/billing",
                    )
                )
            except Exception:
                current_app.logger.exception("Trial reminder email failed for org %s", organization.id)
        reminded += 1
    return reminded


# --- contact requests from the public price list ------------------------------------


def contact_request(data: dict, *, ip: str | None = None) -> None:
    """Somebody on /hinnasto asked for a trial: tell the superadmins."""
    from flask_mail import Message

    from app.core.audit import log_audit
    from app.core.security import normalize_email, validate_email
    from app.email.services import send_system_message
    from app.users.models import User

    name = " ".join(str(data.get("name") or "").split())[:120]
    company = " ".join(str(data.get("company") or "").split())[:160]
    email = normalize_email(str(data.get("email") or ""))
    phone = " ".join(str(data.get("phone") or "").split())[:40]
    message = str(data.get("message") or "").strip()[:2000]
    plan = str(data.get("plan") or "")[:30]
    if not name or not company:
        raise BillingError("Name and company are required.")
    if not email or not validate_email(email):
        raise BillingError("A valid email address is required.")
    log_audit(
        "trial_requested",
        user_id=None,
        organization_id=None,
        target_type="contact_request",
        target_id=None,
        metadata={"company": company, "email": email, "plan": plan},
    )
    db.session.commit()
    recipients = [u.email for u in User.query.filter_by(role="superadmin", is_active=True).all() if u.email]
    if recipients:
        send_system_message(
            Message(
                subject=f"FlowLeads: kokeilupyyntö – {company}",
                recipients=recipients,
                reply_to=email,
                body=(
                    f"Yritys: {company}\nNimi: {name}\nSähköposti: {email}\nPuhelin: {phone or '-'}\n"
                    f"Paketti: {PLANS_BY_KEY.get(plan, {}).get('name', '-')}\n\n{message or '(ei viestiä)'}\n\n"
                    "Luo asiakas tai lähetä kutsu: Asetukset → Kutsu asiakas. Kokeilujakso alkaa, kun asiakas hyväksyy kutsun."
                ),
            )
        )
