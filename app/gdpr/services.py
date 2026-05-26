from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from flask import current_app
from flask_mail import Message

from app.core.audit import log_audit
from app.core.security import check_password
from app.custom_fields.models import CustomFieldValue
from app.extensions import db, mail
from app.gdpr.settings import get_privacy_settings
from app.leads.models import Activity, Lead
from app.leads.services import get_lead_for_org, LeadServiceError
from app.sequences.models import EmailSequenceEnrollment
from app.sequences.services import SequenceService
from app.users.models import User

logger = logging.getLogger(__name__)


class GDPRServiceError(Exception):
    def __init__(self, message: str, code: str = "gdpr_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class GDPRService:
    @staticmethod
    def anonymize_lead(
        lead_id: int,
        requested_by_user_id: int,
        organization_id: int,
        reason: str | None = None,
    ) -> Lead:
        try:
            lead = get_lead_for_org(lead_id, organization_id)
        except LeadServiceError:
            raise GDPRServiceError("Lead not found.", "not_found") from None

        if lead.is_anonymized:
            return lead

        now = datetime.now(timezone.utc)
        lead.email = f"anonymized_{lead.id}@deleted.invalid"
        lead.first_name = "Anonymoitu"
        lead.last_name = "Henkilö"
        lead.phone = None
        lead.linkedin_url = None
        lead.ai_summary = None
        lead.notes = "[Tiedot poistettu GDPR-pyynnöstä]"
        lead.is_anonymized = True
        lead.anonymized_at = now
        lead.marketing_opt_in = False
        lead.gdpr_consent = False
        lead.gdpr_consent_at = None
        lead.gdpr_consent_source = None
        lead.unsubscribed = True
        lead.unsubscribed_at = now
        lead.updated_at = now

        active_enrollments = EmailSequenceEnrollment.query.filter_by(
            lead_id=lead.id,
            organization_id=organization_id,
            status="active",
        ).all()
        for enrollment in active_enrollments:
            SequenceService.unenroll(
                enrollment.id,
                "gdpr_anonymization",
                organization_id=organization_id,
            )

        CustomFieldValue.query.filter_by(
            organization_id=organization_id,
            entity_type="lead",
            entity_id=lead.id,
        ).delete(synchronize_session=False)

        log_audit(
            "gdpr_anonymization_requested",
            user_id=requested_by_user_id,
            organization_id=organization_id,
            target_type="lead",
            target_id=lead.id,
            metadata={"reason": (reason or "")[:500]},
        )

        GDPRService._send_anonymization_confirmation(requested_by_user_id, lead, reason)
        db.session.flush()
        return lead

    @staticmethod
    def verify_password(user: User, password: str) -> None:
        if not password or not check_password(user.password_hash, password):
            raise GDPRServiceError("Invalid password.", "invalid_password")

    @staticmethod
    def _send_anonymization_confirmation(
        user_id: int, lead: Lead, reason: str | None
    ) -> None:
        user = db.session.get(User, user_id)
        if not user or not user.email:
            return
        if not current_app.config.get("EMAIL_SENDING_ENABLED"):
            return
        try:
            msg = Message(
                subject="GDPR anonymisointi suoritettu",
                recipients=[user.email],
                body=(
                    f"Liidi #{lead.id} on anonymisoitu organisaatiossa #{lead.organization_id}.\n"
                    f"Syy: {reason or '—'}\n"
                ),
            )
            mail.send(msg)
        except Exception:
            logger.exception("Failed to send GDPR anonymization confirmation email")

    @staticmethod
    def lead_last_activity_at(lead: Lead) -> datetime:
        candidates = [lead.updated_at, lead.created_at]
        if lead.last_contacted_at:
            candidates.append(lead.last_contacted_at)
        latest = max(c for c in candidates if c is not None)
        if latest.tzinfo is None:
            return latest.replace(tzinfo=timezone.utc)
        return latest

    @staticmethod
    def run_retention_for_organization(organization_id: int) -> list[int]:
        settings = get_privacy_settings(organization_id)
        if not settings.gdpr_auto_anonymize_inactive:
            return []

        retention_days = settings.gdpr_retention_days or 730
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        leads = Lead.query.filter_by(
            organization_id=organization_id,
            is_anonymized=False,
        ).all()

        anonymized_ids: list[int] = []
        for lead in leads:
            if GDPRService.lead_last_activity_at(lead) > cutoff:
                continue
            GDPRService.anonymize_lead(
                lead.id,
                requested_by_user_id=0,
                organization_id=organization_id,
                reason="retention_policy",
            )
            log_audit(
                "gdpr_retention_anonymized",
                organization_id=organization_id,
                target_type="lead",
                target_id=lead.id,
                metadata={"retention_days": retention_days},
            )
            anonymized_ids.append(lead.id)
        return anonymized_ids
