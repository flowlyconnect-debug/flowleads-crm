from __future__ import annotations

import csv
import io
import json
import os
import secrets
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, url_for
from flask_mail import Message

from app.core.audit import log_audit
from app.custom_fields.models import CustomFieldDefinition, CustomFieldValue
from app.custom_fields.services import CustomFieldService
from app.email.models import EmailLog
from app.extensions import db, mail
from app.gdpr.models import EXPORT_REQUEST_STATUSES, EXPORT_TYPES, DataExportRequest
from app.leads.models import Activity, Lead
from app.leads.services import get_lead_for_org, LeadServiceError
from app.sequences.models import EmailSequenceEnrollment, EmailSequenceSent
from app.tasks.models import Task
from app.users.models import AuditLog, User

EXPORT_LINK_HOURS = 48


class DataExportServiceError(Exception):
    def __init__(self, message: str, code: str = "export_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _export_dir() -> Path:
    raw = current_app.config.get("GDPR_EXPORT_DIR", "./exports")
    path = Path(raw).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


class DataExportService:
    @staticmethod
    def export_lead(lead_id: int, organization_id: int) -> dict:
        try:
            lead = get_lead_for_org(lead_id, organization_id)
        except LeadServiceError:
            raise DataExportServiceError("Lead not found.", "not_found") from None

        custom = CustomFieldService.get_values(lead.id, "lead", organization_id)
        activities = (
            Activity.query.filter_by(lead_id=lead.id, organization_id=organization_id)
            .order_by(Activity.created_at.asc())
            .all()
        )
        emails = (
            EmailLog.query.filter_by(lead_id=lead.id, organization_id=organization_id)
            .order_by(EmailLog.created_at.asc())
            .all()
        )
        tasks = (
            Task.query.filter_by(lead_id=lead.id, organization_id=organization_id)
            .order_by(Task.created_at.asc())
            .all()
        )
        enrollments = (
            EmailSequenceEnrollment.query.filter_by(
                lead_id=lead.id, organization_id=organization_id
            )
            .order_by(EmailSequenceEnrollment.enrolled_at.asc())
            .all()
        )
        sequence_sents = (
            EmailSequenceSent.query.filter_by(lead_id=lead.id)
            .order_by(EmailSequenceSent.sent_at.asc())
            .all()
        )
        audit_entries = (
            AuditLog.query.filter_by(
                organization_id=organization_id,
                target_type="lead",
                target_id=lead.id,
            )
            .order_by(AuditLog.created_at.asc())
            .all()
        )

        return {
            "lead": {
                "id": lead.id,
                "organization_id": lead.organization_id,
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "email": lead.email,
                "phone": lead.phone,
                "company": lead.company,
                "title": lead.title,
                "website": lead.website,
                "linkedin_url": lead.linkedin_url,
                "status": lead.status,
                "source": lead.source,
                "source_ref": lead.source_ref,
                "notes": lead.notes,
                "tags": lead.tags,
                "score": lead.score,
                "gdpr_consent": lead.gdpr_consent,
                "gdpr_consent_at": _iso(lead.gdpr_consent_at),
                "gdpr_consent_source": lead.gdpr_consent_source,
                "gdpr_legal_basis": lead.gdpr_legal_basis,
                "marketing_opt_in": lead.marketing_opt_in,
                "unsubscribed": lead.unsubscribed,
                "unsubscribed_at": _iso(lead.unsubscribed_at),
                "is_anonymized": lead.is_anonymized,
                "anonymized_at": _iso(lead.anonymized_at),
                "created_at": _iso(lead.created_at),
                "updated_at": _iso(lead.updated_at),
                "last_contacted_at": _iso(lead.last_contacted_at),
            },
            "custom_fields": custom,
            "activities": [
                {
                    "id": a.id,
                    "type": a.type,
                    "content": a.content,
                    "metadata": a.metadata_json,
                    "user_id": a.user_id,
                    "created_at": _iso(a.created_at),
                }
                for a in activities
            ],
            "emails_sent": [
                {
                    "id": e.id,
                    "subject": e.subject,
                    "status": e.status,
                    "sent_at": _iso(e.sent_at),
                    "created_at": _iso(e.created_at),
                    "gdpr_legal_basis": getattr(e, "gdpr_legal_basis", None),
                }
                for e in emails
            ],
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "type": t.type,
                    "status": t.status,
                    "priority": t.priority,
                    "due_date": _iso(t.due_date),
                    "completed_at": _iso(t.completed_at),
                    "created_at": _iso(t.created_at),
                }
                for t in tasks
            ],
            "sequence_enrollments": [
                {
                    "id": en.id,
                    "sequence_id": en.sequence_id,
                    "status": en.status,
                    "enrolled_at": _iso(en.enrolled_at),
                    "completed_at": _iso(en.completed_at),
                    "cancelled_at": _iso(en.cancelled_at),
                }
                for en in enrollments
            ],
            "sequence_emails_sent": [
                {
                    "id": s.id,
                    "enrollment_id": s.enrollment_id,
                    "step_id": s.step_id,
                    "email_log_id": s.email_log_id,
                    "sent_at": _iso(s.sent_at),
                }
                for s in sequence_sents
            ],
            "audit_log": [
                {
                    "id": entry.id,
                    "action": entry.action,
                    "user_id": entry.user_id,
                    "metadata": entry.metadata_json,
                    "created_at": _iso(entry.created_at),
                }
                for entry in audit_entries
            ],
        }

    @staticmethod
    def _write_csv(path: Path, headers: list[str], rows: list[list]) -> None:
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

    @staticmethod
    def build_organization_zip(organization_id: int) -> Path:
        export_root = _export_dir()
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        work_dir = export_root / f"org_{organization_id}_{ts}"
        work_dir.mkdir(parents=True, exist_ok=True)

        leads = Lead.query.filter_by(organization_id=organization_id).all()
        DataExportService._write_csv(
            work_dir / "leads.csv",
            [
                "id",
                "email",
                "first_name",
                "last_name",
                "company",
                "phone",
                "status",
                "gdpr_consent",
                "marketing_opt_in",
                "unsubscribed",
                "is_anonymized",
                "created_at",
            ],
            [
                [
                    lead.id,
                    lead.email,
                    lead.first_name,
                    lead.last_name,
                    lead.company,
                    lead.phone,
                    lead.status,
                    lead.gdpr_consent,
                    lead.marketing_opt_in,
                    lead.unsubscribed,
                    lead.is_anonymized,
                    _iso(lead.created_at),
                ]
                for lead in leads
            ],
        )

        activities = Activity.query.filter_by(organization_id=organization_id).all()
        DataExportService._write_csv(
            work_dir / "activities.csv",
            ["id", "lead_id", "type", "content", "user_id", "created_at"],
            [
                [
                    a.id,
                    a.lead_id,
                    a.type,
                    (a.content or "")[:500],
                    a.user_id,
                    _iso(a.created_at),
                ]
                for a in activities
            ],
        )

        emails = EmailLog.query.filter_by(organization_id=organization_id).all()
        DataExportService._write_csv(
            work_dir / "emails_sent.csv",
            ["id", "lead_id", "subject", "status", "sent_at", "created_at"],
            [
                [
                    e.id,
                    e.lead_id,
                    e.subject,
                    e.status,
                    _iso(e.sent_at),
                    _iso(e.created_at),
                ]
                for e in emails
            ],
        )

        tasks = Task.query.filter_by(organization_id=organization_id).all()
        DataExportService._write_csv(
            work_dir / "tasks.csv",
            ["id", "lead_id", "title", "type", "status", "due_date", "completed_at"],
            [
                [
                    t.id,
                    t.lead_id,
                    t.title,
                    t.type,
                    t.status,
                    _iso(t.due_date),
                    _iso(t.completed_at),
                ]
                for t in tasks
            ],
        )

        definitions = CustomFieldDefinition.query.filter_by(
            organization_id=organization_id
        ).all()
        values = CustomFieldValue.query.filter_by(organization_id=organization_id).all()
        def_by_id = {d.id: d for d in definitions}
        DataExportService._write_csv(
            work_dir / "custom_fields.csv",
            [
                "definition_id",
                "name",
                "label",
                "entity_type",
                "entity_id",
                "value_text",
                "value_number",
                "value_date",
                "value_boolean",
            ],
            [
                [
                    v.field_definition_id,
                    def_by_id[v.field_definition_id].name
                    if v.field_definition_id in def_by_id
                    else "",
                    def_by_id[v.field_definition_id].label
                    if v.field_definition_id in def_by_id
                    else "",
                    v.entity_type,
                    v.entity_id,
                    v.value_text,
                    v.value_number,
                    _iso(v.value_date) if v.value_date else None,
                    v.value_boolean,
                ]
                for v in values
            ],
        )

        zip_path = export_root / f"org_export_{organization_id}_{ts}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in (
                "leads.csv",
                "activities.csv",
                "emails_sent.csv",
                "tasks.csv",
                "custom_fields.csv",
            ):
                zf.write(work_dir / name, arcname=name)

        for child in work_dir.iterdir():
            child.unlink()
        work_dir.rmdir()
        return zip_path

    @staticmethod
    def create_organization_export_request(
        organization_id: int, requested_by: int
    ) -> DataExportRequest:
        token = secrets.token_urlsafe(32)
        req = DataExportRequest(
            organization_id=organization_id,
            requested_by=requested_by,
            status="pending",
            export_type="organization",
            download_token=token,
        )
        db.session.add(req)
        db.session.flush()
        return req

    @staticmethod
    def process_pending_exports() -> int:
        pending = (
            DataExportRequest.query.filter_by(status="pending")
            .order_by(DataExportRequest.created_at.asc())
            .limit(10)
            .all()
        )
        processed = 0
        for req in pending:
            try:
                DataExportService._process_single_export(req)
                db.session.commit()
                processed += 1
            except Exception as exc:
                db.session.rollback()
                req.status = "failed"
                req.error_message = str(exc)[:500]
                db.session.commit()
        return processed

    @staticmethod
    def _process_single_export(req: DataExportRequest) -> None:
        req.status = "processing"
        db.session.flush()

        if req.export_type == "organization":
            zip_path = DataExportService.build_organization_zip(req.organization_id)
            req.file_path = str(zip_path)
        elif req.export_type == "lead" and req.lead_id:
            payload = DataExportService.export_lead(req.lead_id, req.organization_id)
            export_root = _export_dir()
            filename = f"lead_{req.lead_id}_{req.organization_id}.json"
            path = export_root / filename
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            req.file_path = str(path)
        else:
            raise DataExportServiceError("Invalid export request.", "validation_error")

        now = datetime.now(timezone.utc)
        req.status = "completed"
        req.completed_at = now
        req.expires_at = now + timedelta(hours=EXPORT_LINK_HOURS)
        if not req.download_token:
            req.download_token = secrets.token_urlsafe(32)
        db.session.flush()
        DataExportService._send_download_email(req)

    @staticmethod
    def _send_download_email(req: DataExportRequest) -> None:
        user = db.session.get(User, req.requested_by)
        if not user or not user.email:
            return
        if not current_app.config.get("EMAIL_SENDING_ENABLED"):
            return
        try:
            with current_app.test_request_context():
                download_url = url_for(
                    "settings.gdpr_export_download",
                    token=req.download_token,
                    _external=True,
                )
        except Exception:
            download_url = f"/settings/export/download/{req.download_token}"

        try:
            msg = Message(
                subject="Organisaation tietojen vienti valmis",
                recipients=[user.email],
                body=(
                    f"Tietojen vienti on valmis. Lataa tiedosto 48 tunnin kuluessa:\n{download_url}\n"
                ),
            )
            mail.send(msg)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Failed to send export download email")

    @staticmethod
    def _aware(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    @staticmethod
    def get_export_by_token(token: str) -> DataExportRequest:
        req = DataExportRequest.query.filter_by(download_token=token).first()
        if not req:
            raise DataExportServiceError("Download link not found.", "not_found")
        now = datetime.now(timezone.utc)
        expires = DataExportService._aware(req.expires_at)
        if req.status == "expired" or (expires and expires < now):
            if req.status != "expired":
                req.status = "expired"
                db.session.flush()
            raise DataExportServiceError("Download link has expired.", "expired")
        if req.status != "completed" or not req.file_path:
            raise DataExportServiceError("Export is not ready.", "not_ready")
        if not os.path.isfile(req.file_path):
            raise DataExportServiceError("Export file missing.", "not_found")
        return req
