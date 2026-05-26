"""Scheduler job entry points for GDPR."""

from __future__ import annotations

import logging

from flask import current_app
from flask_mail import Message

from app.extensions import db, mail
from app.gdpr.exports import DataExportService
from app.gdpr.services import GDPRService
from app.tasks.models import OrganizationSettings
from app.users.models import User

logger = logging.getLogger(__name__)


def monthly_gdpr_retention() -> int:
    settings_list = OrganizationSettings.query.filter_by(
        gdpr_auto_anonymize_inactive=True
    ).all()
    total = 0
    for settings in settings_list:
        try:
            ids = GDPRService.run_retention_for_organization(settings.organization_id)
            if ids:
                db.session.commit()
                total += len(ids)
                _notify_retention_report(settings.organization_id, ids)
            else:
                db.session.rollback()
        except Exception:
            db.session.rollback()
            logger.exception(
                "GDPR retention failed for organization %s", settings.organization_id
            )
    return total


def _notify_retention_report(organization_id: int, lead_ids: list[int]) -> None:
    if not current_app.config.get("EMAIL_SENDING_ENABLED"):
        return
    admins = User.query.filter(
        User.organization_id == organization_id,
        User.role.in_(("admin", "superadmin")),
        User.is_active.is_(True),
    ).all()
    recipients = [u.email for u in admins if u.email]
    if not recipients:
        return
    try:
        msg = Message(
            subject="GDPR säilytys: anonymisoidut liidit",
            recipients=recipients,
            body=(
                f"Automaattinen GDPR-säilytys anonymisoi {len(lead_ids)} liidiä.\n"
                f"Liidi-ID:t: {', '.join(str(i) for i in lead_ids[:50])}"
                + ("..." if len(lead_ids) > 50 else "")
            ),
        )
        mail.send(msg)
    except Exception:
        logger.exception("Failed to send GDPR retention report")


def gdpr_export_processor() -> int:
    return DataExportService.process_pending_exports()
