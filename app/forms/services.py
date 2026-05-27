from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, url_for
from app.api.services import (
    ApiServiceError,
    _apply_partial_update,
    find_lead_for_upsert,
    upsert_lead,
)
from app.leads.services import LeadService
from app.core.security import normalize_email
from app.extensions import db
from app.forms.models import LEAD_MAPPED_FIELD_KEYS, WebForm, WebFormSubmission
from app.forms.validators import validate_fields_config, validate_submission_payload
from app.leads.models import Lead
from app.notifications.services import NotificationService

logger = logging.getLogger(__name__)

HONEYPOT_KEYS = ("_hp", "_hp_field", "website_url")


class WebFormServiceError(Exception):
    def __init__(self, message: str, code: str = "error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_form_for_org(form_id: int, organization_id: int) -> WebForm:
    form = WebForm.query.filter_by(
        id=form_id,
        organization_id=organization_id,
        deleted_at=None,
    ).first()
    if not form:
        raise WebFormServiceError("Form not found.", "not_found")
    return form


def get_active_form_by_token(form_token: str) -> WebForm | None:
    return WebForm.query.filter_by(
        form_token=form_token,
        is_active=True,
        deleted_at=None,
    ).first()


def find_lead_for_form_upsert(
    organization_id: int,
    *,
    email: str | None,
    phone: str | None,
    source: str,
    source_ref: str | None,
) -> Lead | None:
    lead = find_lead_for_upsert(
        organization_id,
        email=email,
        source=source,
        source_ref=source_ref,
    )
    if lead:
        return lead
    if phone:
        normalized_phone = str(phone).strip()
        if normalized_phone:
            return Lead.query.filter_by(
                organization_id=organization_id,
                phone=normalized_phone,
            ).first()
    return None


def _submission_to_lead_payload(form: WebForm, data: dict) -> dict:
    payload = {k: v for k, v in data.items() if k in LEAD_MAPPED_FIELD_KEYS}
    payload["source"] = "webform"
    payload["source_ref"] = form.name
    if payload.get("email"):
        payload["email"] = normalize_email(payload["email"])
    return payload


def _is_spam_submission(payload: dict) -> bool:
    for key in HONEYPOT_KEYS:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return True
    return False


def _is_duplicate_submission(form: WebForm, email: str | None) -> bool:
    if not email:
        return False
    normalized = normalize_email(email)
    if not normalized:
        return False
    cutoff = _utc_now() - timedelta(minutes=5)
    recent = (
        WebFormSubmission.query.filter(
            WebFormSubmission.form_id == form.id,
            WebFormSubmission.organization_id == form.organization_id,
            WebFormSubmission.created_at >= cutoff,
            WebFormSubmission.status.in_(("processed", "duplicate")),
        )
        .order_by(WebFormSubmission.created_at.desc())
        .limit(20)
        .all()
    )
    for sub in recent:
        sub_email = sub.raw_data.get("email") if isinstance(sub.raw_data, dict) else None
        if sub_email and normalize_email(str(sub_email)) == normalized:
            return True
    return False


def _record_submission(
    form: WebForm,
    *,
    raw_data: dict,
    request_meta: dict,
    status: str,
    lead_id: int | None = None,
) -> WebFormSubmission:
    submission = WebFormSubmission(
        form_id=form.id,
        organization_id=form.organization_id,
        lead_id=lead_id,
        raw_data=raw_data,
        ip_address=(request_meta.get("ip") or "")[:64] or None,
        user_agent=(request_meta.get("user_agent") or "")[:500] or None,
        status=status,
    )
    db.session.add(submission)
    if status == "processed":
        form.submission_count = (form.submission_count or 0) + 1
    db.session.flush()
    return submission


def _apply_form_defaults(form: WebForm, lead: Lead) -> None:
    if form.default_stage_id:
        lead.stage_id = form.default_stage_id
    if form.default_assigned_to:
        lead.assigned_to = form.default_assigned_to


def _enroll_sequence(form: WebForm, lead: Lead) -> None:
    if not form.auto_enroll_sequence_id:
        return
    try:
        from app.sequences.services import SequenceService, SequenceServiceError

        SequenceService.enroll_lead(
            lead.id,
            form.auto_enroll_sequence_id,
            enrolled_by=None,
            organization_id=form.organization_id,
        )
    except SequenceServiceError:
        logger.warning(
            "Form %s auto-enroll failed for lead %s sequence %s",
            form.id,
            lead.id,
            form.auto_enroll_sequence_id,
        )
    except Exception:
        logger.exception("Form auto-enroll failed form=%s lead=%s", form.id, lead.id)


def _notify_users(form: WebForm, lead: Lead) -> None:
    user_ids = form.notify_users or []
    if not user_ids:
        return
    lead_name = lead.display_name if hasattr(lead, "display_name") else (
        f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "Uusi liidi"
    )
    company = (lead.company or "").strip() or "—"
    timestamp = _utc_now().strftime("%d.%m.%Y %H:%M")
    title = f"Uusi lomakelähetys: {form.name}"
    message = f"{lead_name} ({company}) — {timestamp}"
    link = f"/leads/{lead.id}"
    for uid in user_ids:
        try:
            uid_int = int(uid)
        except (TypeError, ValueError):
            continue
        try:
            NotificationService.create(
                user_id=uid_int,
                organization_id=form.organization_id,
                type="webform_submission",
                title=title,
                message=message,
                link=link,
            )
        except Exception:
            logger.exception(
                "Failed to notify user %s for form submission %s", uid_int, form.id
            )


class WebFormService:
    @staticmethod
    def create_form(data: dict, organization_id: int, user_id: int) -> WebForm:
        fields = data.get("fields") or []
        ok, msg = validate_fields_config(fields)
        if not ok:
            raise WebFormServiceError(msg or "Invalid fields.", "validation_error")

        name = (data.get("name") or "").strip()
        title = (data.get("title") or "").strip()
        if not name:
            raise WebFormServiceError("Name is required.", "validation_error")
        if not title:
            raise WebFormServiceError("Title is required.", "validation_error")

        form = WebForm(
            organization_id=organization_id,
            name=name,
            form_token=secrets.token_urlsafe(24),
            title=title,
            description=(data.get("description") or "").strip() or None,
            submit_button_text=(data.get("submit_button_text") or "Lähetä").strip()[:100],
            success_message=(
                data.get("success_message") or "Kiitos! Otamme yhteyttä pian."
            ).strip()[:500],
            fields=fields,
            default_stage_id=data.get("default_stage_id"),
            default_assigned_to=data.get("default_assigned_to"),
            auto_enroll_sequence_id=data.get("auto_enroll_sequence_id"),
            notify_users=data.get("notify_users") or [],
            is_active=bool(data.get("is_active", True)),
            created_by=user_id,
        )
        db.session.add(form)
        db.session.flush()
        return form

    @staticmethod
    def update_form(form_id: int, data: dict, organization_id: int) -> WebForm:
        form = get_form_for_org(form_id, organization_id)
        if "fields" in data:
            ok, msg = validate_fields_config(data["fields"])
            if not ok:
                raise WebFormServiceError(msg or "Invalid fields.", "validation_error")
            form.fields = data["fields"]
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                raise WebFormServiceError("Name is required.", "validation_error")
            form.name = name
        if "title" in data:
            title = (data.get("title") or "").strip()
            if not title:
                raise WebFormServiceError("Title is required.", "validation_error")
            form.title = title
        for key in (
            "description",
            "submit_button_text",
            "success_message",
            "default_stage_id",
            "default_assigned_to",
            "auto_enroll_sequence_id",
            "is_active",
        ):
            if key in data:
                setattr(form, key, data[key])
        if "notify_users" in data:
            raw_users = data["notify_users"] or []
            notify: list[int] = []
            for uid in raw_users:
                try:
                    notify.append(int(uid))
                except (TypeError, ValueError):
                    continue
            form.notify_users = notify
        form.updated_at = _utc_now()
        db.session.flush()
        return form

    @staticmethod
    def soft_delete_form(form_id: int, organization_id: int) -> None:
        form = get_form_for_org(form_id, organization_id)
        form.is_active = False
        form.deleted_at = _utc_now()
        db.session.flush()

    @staticmethod
    def list_for_organization(organization_id: int) -> list[WebForm]:
        return (
            WebForm.query.filter_by(organization_id=organization_id, deleted_at=None)
            .order_by(WebForm.created_at.desc())
            .all()
        )

    @staticmethod
    def list_submissions(form_id: int, organization_id: int) -> list[WebFormSubmission]:
        get_form_for_org(form_id, organization_id)
        return (
            WebFormSubmission.query.filter_by(
                form_id=form_id,
                organization_id=organization_id,
            )
            .order_by(WebFormSubmission.created_at.desc())
            .all()
        )

    @staticmethod
    def public_form_dict(form: WebForm) -> dict:
        return {
            "title": form.title,
            "description": form.description or "",
            "submit_button_text": form.submit_button_text,
            "fields": form.fields or [],
        }

    @staticmethod
    def submit(form_token: str, payload: dict, request_meta: dict) -> dict:
        form = get_active_form_by_token(form_token)
        if not form:
            raise WebFormServiceError("Form not found.", "not_found")

        if not isinstance(payload, dict):
            return {
                "success": False,
                "error": {
                    "code": "validation_error",
                    "message": "Invalid submission.",
                },
            }

        if _is_spam_submission(payload):
            _record_submission(
                form,
                raw_data=dict(payload),
                request_meta=request_meta,
                status="spam",
            )
            return {
                "success": True,
                "message": form.success_message,
            }

        normalized, validation_error = validate_submission_payload(
            form.fields or [],
            payload,
        )
        if validation_error:
            return {"success": False, "error": validation_error}

        email = normalized.get("email") if normalized else None
        if _is_duplicate_submission(form, email):
            _record_submission(
                form,
                raw_data=normalized,
                request_meta=request_meta,
                status="duplicate",
            )
            return {
                "success": False,
                "error": {
                    "code": "duplicate_submission",
                    "message": "Duplicate submission detected.",
                },
            }

        normalized_data = normalized
        try:
            lead_payload = _submission_to_lead_payload(form, normalized_data)
            existing = find_lead_for_form_upsert(
                form.organization_id,
                email=lead_payload.get("email"),
                phone=lead_payload.get("phone"),
                source="webform",
                source_ref=form.name,
            )
            if existing:
                _apply_partial_update(existing, lead_payload)
                existing.updated_at = _utc_now()
                db.session.flush()
                LeadService.log_activity(
                    existing.id,
                    None,
                    "updated",
                    content=f"Updated via web form: {form.name}",
                )
                lead = existing
            else:
                lead, _action = upsert_lead(form.organization_id, lead_payload)
            _apply_form_defaults(form, lead)
            lead.updated_at = _utc_now()
            db.session.flush()

            _enroll_sequence(form, lead)
            _notify_users(form, lead)

            submission = _record_submission(
                form,
                raw_data=normalized,
                request_meta=request_meta,
                status="processed",
                lead_id=lead.id,
            )

            return {
                "success": True,
                "message": form.success_message,
                "submission_id": submission.id,
            }
        except ApiServiceError as exc:
            logger.warning("Web form lead validation failed: %s", exc.message)
            db.session.rollback()
            return {
                "success": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            }
        except Exception:
            logger.exception("Web form submission failed token=%s", form_token)
            db.session.rollback()
            form_retry = get_active_form_by_token(form_token)
            fail_data = normalized_data
            if form_retry:
                try:
                    _record_submission(
                        form_retry,
                        raw_data=fail_data if isinstance(fail_data, dict) else {},
                        request_meta=request_meta,
                        status="failed",
                    )
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            return {
                "success": False,
                "error": {
                    "code": "server_error",
                    "message": "Unable to process your submission. Please try again later.",
                },
            }

    @staticmethod
    def submissions_today_count(organization_id: int) -> int:
        start = _utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            WebFormSubmission.query.filter(
                WebFormSubmission.organization_id == organization_id,
                WebFormSubmission.status == "processed",
                WebFormSubmission.created_at >= start,
            ).count()
        )

    @staticmethod
    def generate_embed_code(form_id: int, organization_id: int) -> dict:
        form = get_form_for_org(form_id, organization_id)
        base = current_app.config.get("PUBLIC_BASE_URL")
        if not base:
            try:
                base = url_for("index", _external=True).rstrip("/")
            except RuntimeError:
                base = "https://app.flowleads.fi"
        script_src = f"{base}/static/forms/embed.js"
        iframe_src = f"{base}/forms/{form.form_token}/embed"
        script_embed = (
            f'<div id="flowleads-form"></div>\n'
            f'<script src="{script_src}"\n'
            f'        data-form-token="{form.form_token}"\n'
            f'        data-target="#flowleads-form"></script>'
        )
        iframe_embed = (
            f'<iframe src="{iframe_src}" title="{form.title}" '
            f'style="width:100%;min-height:520px;border:0;border-radius:8px;" '
            f'loading="lazy"></iframe>'
        )
        return {"script": script_embed, "iframe": iframe_embed, "form_token": form.form_token}
