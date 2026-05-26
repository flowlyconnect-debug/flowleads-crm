from __future__ import annotations

from flask import current_app

from app.email.models import EmailTemplate
from app.email.services import EmailServiceError, _mailgun_send, _resolve_sender, email_sending_enabled
from app.email.templates import build_task_reminder_context, render_template_text
from app.tasks.models import Task
from app.users.models import Organization


def send_task_reminder_email(task: Task) -> None:
    if not email_sending_enabled():
        raise EmailServiceError("Email sending is disabled.", "email_disabled")

    assignee = task.assignee
    if not assignee or not assignee.email:
        raise EmailServiceError("Assignee has no email.", "no_recipient")

    template = EmailTemplate.query.filter_by(
        organization_id=None,
        name="task_reminder",
    ).first()
    if not template:
        raise EmailServiceError("task_reminder template not found.", "no_template")

    org = Organization.query.get(task.organization_id)
    if not org:
        raise EmailServiceError("Organization not found.", "no_org")

    context = build_task_reminder_context(task)
    subject = render_template_text(template.subject_template, context)
    body_html = render_template_text(template.body_html_template or "", context)
    body_text = render_template_text(template.body_text_template or "", context)

    from_name, from_email = _resolve_sender(org)
    ok, message_id, error = _mailgun_send(
        to_email=assignee.email,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        from_name=from_name,
        from_email=from_email,
    )
    if not ok:
        raise EmailServiceError(error or "Failed to send reminder.", "send_failed")

    current_app.logger.info(
        "Task reminder sent task_id=%s message_id=%s",
        task.id,
        message_id,
    )
