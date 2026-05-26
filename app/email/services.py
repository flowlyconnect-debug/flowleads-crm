import hashlib
import hmac
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import current_app

from app.core.audit import log_audit
from app.core.security import validate_email
from app.email.models import EMAIL_LOG_STATUSES, EmailLog, EmailTemplate
from app.email.seed import seed_system_email_templates
from app.email.templates import (
    body_preview,
    build_template_context,
    render_template_text,
    validate_template_variables,
)
from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import get_lead_for_org
from app.users.models import Organization, User

MAX_SUBJECT_LEN = 255
SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
EVENT_HANDLER_RE = re.compile(r"\s+on\w+\s*=", re.IGNORECASE)
JAVASCRIPT_URL_RE = re.compile(r"javascript:", re.IGNORECASE)


class EmailServiceError(Exception):
    def __init__(self, message: str, code: str = "email_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def email_sending_enabled() -> bool:
    return bool(current_app.config.get("EMAIL_SENDING_ENABLED"))


def sanitize_html_body(html_body: str | None) -> str:
    if not html_body:
        return ""
    cleaned = SCRIPT_TAG_RE.sub("", html_body)
    cleaned = EVENT_HANDLER_RE.sub(" disabled=", cleaned)
    cleaned = JAVASCRIPT_URL_RE.sub("", cleaned)
    return cleaned


def html_to_plaintext(html_body: str | None) -> str:
    if not html_body:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html_body, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def plaintext_to_html(text: str | None) -> str:
    if not text:
        return "<p></p>"
    escaped = html.escape(text)
    paragraphs = [f"<p>{p.replace(chr(10), '<br>')}</p>" for p in escaped.split("\n\n") if p.strip()]
    return "\n".join(paragraphs) if paragraphs else f"<p>{escaped}</p>"


def _safe_mailgun_error(exc: Exception) -> str:
    msg = str(exc)
    if "401" in msg or "403" in msg or "Forbidden" in msg:
        return "Email provider authentication failed."
    if "timeout" in msg.lower():
        return "Email provider request timed out."
    if len(msg) > 500:
        return msg[:500]
    return msg or "Email send failed."


def _resolve_sender(organization: Organization) -> tuple[str, str]:
    from_name = (organization.email_from_name or "").strip()
    from_email = (organization.email_from_email or "").strip()
    if from_email and validate_email(from_email):
        name = from_name or current_app.config.get("MAILGUN_FROM_NAME", "FlowLeads")
        return name, from_email
    default_email = (current_app.config.get("MAILGUN_FROM_EMAIL") or "").strip()
    default_name = current_app.config.get("MAILGUN_FROM_NAME", "FlowLeads")
    if not default_email and current_app.config.get("TESTING"):
        default_email = "test@example.com"
        default_name = default_name or "FlowLeads Test"
    return default_name, default_email


def _mailgun_send(
    *,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    from_name: str,
    from_email: str,
) -> tuple[bool, str | None, str | None]:
    api_key = current_app.config.get("MAILGUN_API_KEY")
    domain = current_app.config.get("MAILGUN_DOMAIN")
    org_domain_override = None

    if not api_key or not domain:
        return False, None, "Email sending is not configured."

    from_header = f"{from_name} <{from_email}>"
    data = urllib.parse.urlencode(
        {
            "from": from_header,
            "to": to_email,
            "subject": subject,
            "html": body_html,
            "text": body_text,
        }
    ).encode("utf-8")

    url = f"https://api.mailgun.net/v3/{domain}/messages"
    request = urllib.request.Request(url, data=data, method="POST")
    import base64

    token = base64.b64encode(f"api:{api_key}".encode()).decode("ascii")
    request.add_header("Authorization", f"Basic {token}")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            message_id = payload.get("id")
            return True, message_id, None
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
            err_json = json.loads(err_body)
            err_msg = err_json.get("message", "Mailgun request failed.")
        except (json.JSONDecodeError, ValueError):
            err_msg = f"Mailgun HTTP {exc.code}"
        return False, None, _safe_mailgun_error(Exception(err_msg))
    except Exception as exc:
        return False, None, _safe_mailgun_error(exc)


class EmailService:
    @staticmethod
    def send_to_lead(
        lead_id: int,
        user_id: int | None,
        subject: str,
        body_html: str | None,
        body_text: str | None,
        *,
        organization_id: int,
        actor: User | None = None,
    ) -> dict:
        if not email_sending_enabled():
            raise EmailServiceError("Email sending is disabled.", "sending_disabled")

        subject = (subject or "").strip()[:MAX_SUBJECT_LEN]
        if not subject:
            raise EmailServiceError("Subject is required.", "validation_error")

        lead = get_lead_for_org(lead_id, organization_id)
        if not lead.email:
            raise EmailServiceError("Lead has no email address.", "no_email")

        if actor and actor.organization_id is not None:
            if actor.organization_id != lead.organization_id and not actor.is_superadmin():
                raise EmailServiceError("Access denied.", "forbidden")

        body_html = sanitize_html_body(body_html or "")
        body_text = (body_text or "").strip() or html_to_plaintext(body_html)
        if not body_html and body_text:
            body_html = plaintext_to_html(body_text)

        max_body = current_app.config.get("EMAIL_MAX_BODY_CHARS", 100_000)
        if len(body_html) > max_body or len(body_text) > max_body:
            raise EmailServiceError("Email body is too large.", "validation_error")

        org = db.session.get(Organization, lead.organization_id)
        from_name, from_email = _resolve_sender(org)
        if not from_email or not validate_email(from_email):
            raise EmailServiceError("Sender email is not configured.", "sender_not_configured")

        preview = body_preview(body_html or body_text)
        log = EmailLog(
            lead_id=lead.id,
            user_id=user_id,
            organization_id=lead.organization_id,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            status="failed",
        )
        db.session.add(log)
        db.session.flush()

        ok, message_id, error = _mailgun_send(
            to_email=lead.email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            from_name=from_name,
            from_email=from_email,
        )

        if ok:
            log.status = "sent"
            log.mailgun_message_id = message_id
            log.sent_at = datetime.now(timezone.utc)
            lead.last_contacted_at = log.sent_at
            activity = Activity(
                lead_id=lead.id,
                user_id=user_id,
                organization_id=lead.organization_id,
                type="email_sent",
                content=subject,
                metadata_json={
                    "message_id": message_id,
                    "body_preview": preview,
                    "email_log_id": log.id,
                },
            )
            db.session.add(activity)
            log_audit(
                "email_sent",
                user_id=user_id,
                organization_id=lead.organization_id,
                target_type="email_log",
                target_id=log.id,
                metadata={"lead_id": lead.id, "subject": subject[:120]},
            )
            return {"success": True, "email_log_id": log.id, "message_id": message_id}

        log.error_message = error
        log_audit(
            "email_failed",
            user_id=user_id,
            organization_id=lead.organization_id,
            target_type="email_log",
            target_id=log.id,
            metadata={"lead_id": lead.id, "error": (error or "")[:200]},
        )
        return {"success": False, "email_log_id": log.id, "error": error}


class TemplateService:
    @staticmethod
    def list_for_organization(organization_id: int) -> list[EmailTemplate]:
        seed_system_email_templates()
        return (
            EmailTemplate.query.filter(
                (EmailTemplate.organization_id == organization_id)
                | (EmailTemplate.organization_id.is_(None))
            )
            .order_by(EmailTemplate.organization_id.asc().nullsfirst(), EmailTemplate.name.asc())
            .all()
        )

    @staticmethod
    def get_template(template_id: int, organization_id: int | None) -> EmailTemplate:
        template = db.session.get(EmailTemplate, template_id)
        if not template:
            raise EmailServiceError("Template not found.", "not_found")
        if template.organization_id is not None and template.organization_id != organization_id:
            raise EmailServiceError("Template not found.", "not_found")
        return template

    @staticmethod
    def render_for_lead(template: EmailTemplate, lead: Lead, sender_name: str) -> dict[str, str]:
        context = build_template_context(lead, sender_name)
        return {
            "subject": render_template_text(template.subject_template, context),
            "body_html": render_template_text(template.body_html_template, context),
            "body_text": render_template_text(template.body_text_template or "", context),
        }

    @staticmethod
    def create_template(
        organization_id: int,
        user_id: int,
        *,
        name: str,
        subject_template: str,
        body_html_template: str,
        body_text_template: str | None = None,
        variables: list | None = None,
    ) -> EmailTemplate:
        ok, err = validate_template_variables(
            subject_template, body_html_template, body_text_template or ""
        )
        if not ok:
            raise EmailServiceError(err, "validation_error")
        template = EmailTemplate(
            organization_id=organization_id,
            name=name.strip()[:120],
            subject_template=subject_template.strip()[:255],
            body_html_template=body_html_template,
            body_text_template=body_text_template,
            variables=variables,
            created_by=user_id,
        )
        db.session.add(template)
        db.session.flush()
        log_audit(
            "email_template_created",
            user_id=user_id,
            organization_id=organization_id,
            target_type="email_template",
            target_id=template.id,
            metadata={"name": template.name},
        )
        return template

    @staticmethod
    def update_template(template: EmailTemplate, user_id: int, **fields) -> EmailTemplate:
        ok, err = validate_template_variables(
            fields.get("subject_template", template.subject_template),
            fields.get("body_html_template", template.body_html_template),
            fields.get("body_text_template", template.body_text_template or ""),
        )
        if not ok:
            raise EmailServiceError(err, "validation_error")
        for key, value in fields.items():
            if hasattr(template, key) and value is not None:
                setattr(template, key, value)
        log_audit(
            "email_template_updated",
            user_id=user_id,
            organization_id=template.organization_id,
            target_type="email_template",
            target_id=template.id,
            metadata={"name": template.name},
        )
        return template

    @staticmethod
    def delete_template(template: EmailTemplate, user_id: int) -> None:
        if template.is_system:
            raise EmailServiceError("System templates cannot be deleted.", "forbidden")
        log_audit(
            "email_template_deleted",
            user_id=user_id,
            organization_id=template.organization_id,
            target_type="email_template",
            target_id=template.id,
            metadata={"name": template.name},
        )
        db.session.delete(template)


def get_email_log_for_org(email_log_id: int, organization_id: int) -> EmailLog:
    log = EmailLog.query.filter_by(id=email_log_id, organization_id=organization_id).first()
    if not log:
        raise EmailServiceError("Email not found.", "not_found")
    return log


def list_email_logs_for_lead(lead_id: int, organization_id: int) -> list[EmailLog]:
    get_lead_for_org(lead_id, organization_id)
    return (
        EmailLog.query.filter_by(lead_id=lead_id, organization_id=organization_id)
        .order_by(EmailLog.created_at.desc())
        .all()
    )


def update_organization_email_settings(
    organization: Organization,
    *,
    email_from_name: str | None,
    email_from_email: str | None,
    mailgun_domain: str | None,
    user_id: int,
) -> Organization:
    if email_from_email and not validate_email(email_from_email):
        raise EmailServiceError("Invalid from email address.", "validation_error")
    organization.email_from_name = (email_from_name or "").strip()[:120] or None
    organization.email_from_email = (email_from_email or "").strip()[:255] or None
    organization.mailgun_domain = (mailgun_domain or "").strip()[:255] or None
    log_audit(
        "email_settings_updated",
        user_id=user_id,
        organization_id=organization.id,
        target_type="organization",
        target_id=organization.id,
    )
    return organization


def verify_mailgun_webhook_signature(timestamp: str, token: str, signature: str) -> bool:
    signing_key = current_app.config.get("MAILGUN_WEBHOOK_SIGNING_KEY")
    if not signing_key:
        return False
    payload = f"{timestamp}{token}".encode("utf-8")
    digest = hmac.new(signing_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def handle_mailgun_webhook(event_data: dict) -> bool:
    event = (event_data.get("event") or "").lower()
    message_headers = event_data.get("message", {}) or {}
    if isinstance(message_headers, dict):
        headers = message_headers.get("headers") or {}
        message_id = headers.get("message-id") if isinstance(headers, dict) else None
    else:
        message_id = None
    if not message_id:
        message_id = event_data.get("message-id")
    if not message_id:
        return False

    log = EmailLog.query.filter_by(mailgun_message_id=message_id).first()
    if not log:
        return False

    status_map = {
        "delivered": "delivered",
        "bounced": "bounced",
        "failed": "failed",
        "opened": "opened",
        "clicked": "clicked",
    }
    new_status = status_map.get(event)
    if new_status and new_status in EMAIL_LOG_STATUSES:
        log.status = new_status
        log.updated_at = datetime.now(timezone.utc)

    if event == "opened":
        activity = Activity(
            lead_id=log.lead_id,
            user_id=log.user_id,
            organization_id=log.organization_id,
            type="email_opened",
            content=log.subject,
            metadata_json={"email_log_id": log.id, "event": "opened", "message_id": message_id},
        )
        db.session.add(activity)
    elif event == "clicked":
        activity = Activity(
            lead_id=log.lead_id,
            user_id=log.user_id,
            organization_id=log.organization_id,
            type="email_clicked",
            content=log.subject,
            metadata_json={"email_log_id": log.id, "event": "clicked", "message_id": message_id},
        )
        db.session.add(activity)

    log_audit(
        "email_webhook_received",
        organization_id=log.organization_id,
        target_type="email_log",
        target_id=log.id,
        metadata={"event": event, "message_id": message_id},
    )
    return True


def send_test_email_to_user(user: User) -> dict:
    if not user.email:
        raise EmailServiceError("User has no email.", "validation_error")
    return send_test_email_to_address(user.email, user_id=user.id)


def send_test_email_to_address(email: str, *, user_id: int | None = None) -> dict:
    if not email_sending_enabled():
        raise EmailServiceError("Email sending is disabled.", "sending_disabled")

    from_name = current_app.config.get("MAILGUN_FROM_NAME", "FlowLeads")
    from_email = current_app.config.get("MAILGUN_FROM_EMAIL")
    ok, message_id, error = _mailgun_send(
        to_email=email,
        subject="FlowLeads test email",
        body_html="<p>This is a test email from FlowLeads.</p>",
        body_text="This is a test email from FlowLeads.",
        from_name=from_name,
        from_email=from_email or "",
    )
    if ok:
        log_audit(
            "email_test_sent",
            user_id=user_id,
            metadata={"message_id": message_id, "to": email},
        )
        return {"success": True, "message_id": message_id}
    log_audit(
        "email_test_sent",
        user_id=user_id,
        metadata={"success": False, "error": (error or "")[:200]},
    )
    return {"success": False, "error": error}


def paginate_email_logs(page: int = 1, per_page: int = 25, organization_id: int | None = None):
    query = EmailLog.query.order_by(EmailLog.created_at.desc())
    if organization_id:
        query = query.filter_by(organization_id=organization_id)
    return query.paginate(page=page, per_page=per_page, error_out=False)
