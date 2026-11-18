"""Asiakastiedotteet (superadmin): one e-mail to many customers through
Mailgun, e.g. about an update. Sent from the platform sender
(MAILGUN_FROM_NAME / MAILGUN_FROM_EMAIL), replies go to the superadmin who sent
it. `{yritys}` in the subject or text becomes the customer's name."""

from __future__ import annotations

import html
from dataclasses import dataclass

from flask import current_app

from app.extensions import db

AUDIENCES = {
    "admins": "Asiakkaiden pääkäyttäjät",
    "all": "Kaikki asiakkaiden käyttäjät",
}
MAX_SUBJECT = 200
MAX_BODY = 20000


class AnnouncementError(ValueError):
    pass


@dataclass
class Recipient:
    email: str
    organization_name: str


def recipients(audience: str, organization_ids: list[int] | None = None) -> list[Recipient]:
    """Active users of active customer organizations (never superadmins),
    each address once. `organization_ids` empty = every active customer."""
    from app.users.models import Organization, User

    if audience not in AUDIENCES:
        raise AnnouncementError("Choose who receives the message.")
    query = (
        db.session.query(User.email, Organization.name)
        .join(Organization, Organization.id == User.organization_id)
        .filter(User.is_active.is_(True), Organization.is_active.is_(True), User.role != "superadmin")
    )
    if audience == "admins":
        query = query.filter(User.role == "admin")
    if organization_ids:
        query = query.filter(Organization.id.in_(organization_ids))
    seen: set[str] = set()
    result = []
    for email, name in query.order_by(Organization.name, User.email):
        key = (email or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(Recipient(email.strip(), name))
    return result


def _validate(subject: str, body: str) -> tuple[str, str]:
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject:
        raise AnnouncementError("Write a subject.")
    if not body:
        raise AnnouncementError("Write the message.")
    if len(subject) > MAX_SUBJECT or len(body) > MAX_BODY:
        raise AnnouncementError("The message is too long.")
    return subject, body


def render(subject: str, body: str, organization_name: str) -> tuple[str, str, str]:
    """(subject, html, text) for one customer."""

    def fill(text: str) -> str:
        return text.replace("{yritys}", organization_name or "")

    subject, text = fill(subject), fill(body)
    paragraphs = "".join(
        f"<p style='margin:0 0 14px;'>{html.escape(part).replace(chr(10), '<br>')}</p>"
        for part in text.replace("\r\n", "\n").split("\n\n")
        if part.strip()
    )
    footer = "Saat tämän viestin FlowLeadsin asiakkaana. Voit vastata suoraan tähän viestiin."
    body_html = (
        "<div style='font-family:Arial,sans-serif;font-size:15px;line-height:1.55;color:#172033;max-width:600px;'>"
        f"{paragraphs}"
        f"<p style='margin-top:24px;color:#526079;font-size:12px;'>{html.escape(footer)}</p>"
        "</div>"
    )
    return subject, body_html, f"{text}\n\n--\n{footer}"


def send_announcement(
    *,
    subject: str,
    body: str,
    audience: str,
    organization_ids: list[int] | None,
    sender,
    test_only: bool = False,
):
    """Send to every recipient, or only to the sender (`test_only`). Returns
    the stored CustomerAnnouncement (None for a test) and the outcome."""
    from app.admin.models import CustomerAnnouncement
    from app.core.audit import log_audit
    from app.email.services import _mailgun_send, email_sending_enabled

    subject, body = _validate(subject, body)
    if not email_sending_enabled():
        raise AnnouncementError("Email sending is not enabled.")
    from_name = current_app.config.get("MAILGUN_FROM_NAME", "FlowLeads")
    from_email = current_app.config.get("MAILGUN_FROM_EMAIL") or ""
    if not from_email:
        raise AnnouncementError("The sender address is not set.")
    if test_only:
        targets = [Recipient(sender.email, "Esimerkki Oy")]
    else:
        targets = recipients(audience, organization_ids)
        if not targets:
            raise AnnouncementError("No recipients.")
    failures = []
    for target in targets:
        mail_subject, body_html, body_text = render(subject, body, target.organization_name)
        ok, _message_id, error = _mailgun_send(
            to_email=target.email,
            subject=("[Testi] " if test_only else "") + mail_subject,
            body_html=body_html,
            body_text=body_text,
            from_name=from_name,
            from_email=from_email,
            reply_to=sender.email,
        )
        if not ok:
            failures.append({"email": target.email, "error": (error or "")[:200]})
    outcome = {"sent": len(targets) - len(failures), "failed": len(failures), "failures": failures}
    if test_only:
        return None, outcome
    announcement = CustomerAnnouncement(
        subject=subject,
        body=body,
        audience=audience,
        organization_ids=list(organization_ids or []) or None,
        sent_by_user_id=sender.id,
        recipients=len(targets),
        failed=len(failures),
        failures=failures or None,
    )
    db.session.add(announcement)
    log_audit(
        "announcement_sent",
        user_id=sender.id,
        target_type="customer_announcement",
        metadata={"subject": subject[:120], "recipients": len(targets), "failed": len(failures), "audience": audience},
    )
    db.session.commit()
    return announcement, outcome


def recent_announcements(limit: int = 20):
    from app.admin.models import CustomerAnnouncement

    return CustomerAnnouncement.query.order_by(CustomerAnnouncement.sent_at.desc()).limit(limit).all()
