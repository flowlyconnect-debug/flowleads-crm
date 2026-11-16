from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, url_for
from app.api.services import (
    ApiServiceError,
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
    phone: str | None = None,
    source: str | None = None,
    source_ref: str | None = None,
) -> Lead | None:
    """The lead an anonymous submission belongs to: same email in the same
    organization only. Never by phone or by the form's name: anybody who
    knows a phone number (or just submits the form twice) used to take over
    an existing lead's contact details."""
    normalized = normalize_email(email or "")
    if not normalized:
        return None
    return Lead.query.filter_by(organization_id=organization_id, email=normalized).order_by(Lead.id).first()


def _fill_empty_fields(lead: Lead, payload: dict) -> list[str]:
    """An unauthenticated submission may add missing details to a lead but
    never overwrite what the customer already has."""
    filled = []
    for field in ("first_name", "last_name", "phone", "company", "title", "website", "linkedin_url"):
        value = payload.get(field)
        if value and not getattr(lead, field, None):
            setattr(lead, field, value)
            filled.append(field)
    return filled


def _submission_to_lead_payload(form: WebForm, data: dict) -> dict:
    payload = {k: v for k, v in data.items() if k in LEAD_MAPPED_FIELD_KEYS}
    payload["source"] = "webform"
    # unique per submission: (source, source_ref) identifies a lead
    payload["source_ref"] = f"{(form.name or 'Lomake')[:200]} #{secrets.token_hex(4)}"
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


def _own_id(model, organization_id: int, value, *, active_user: bool = False) -> int | None:
    """An id from the request that must belong to this organization (a stage,
    user or sequence of another customer is rejected, not stored)."""
    if value in (None, "", 0, "0"):
        return None
    try:
        ident = int(value)
    except (TypeError, ValueError):
        raise WebFormServiceError("Invalid reference.", "validation_error") from None
    row = db.session.get(model, ident)
    if row is None or getattr(row, "organization_id", None) != organization_id:
        raise WebFormServiceError("Invalid reference.", "validation_error")
    return ident


def _clean_form_references(data: dict, organization_id: int) -> dict:
    from app.leads.models import PipelineStage
    from app.sequences.models import EmailSequence
    from app.users.models import User

    clean = {}
    if "default_stage_id" in data:
        clean["default_stage_id"] = _own_id(PipelineStage, organization_id, data.get("default_stage_id"))
    if "default_assigned_to" in data:
        clean["default_assigned_to"] = _own_id(User, organization_id, data.get("default_assigned_to"))
    if "auto_enroll_sequence_id" in data:
        clean["auto_enroll_sequence_id"] = _own_id(EmailSequence, organization_id, data.get("auto_enroll_sequence_id"))
    if "notify_users" in data:
        users = []
        for uid in data.get("notify_users") or []:
            try:
                users.append(_own_id(User, organization_id, uid))
            except WebFormServiceError:
                continue
        clean["notify_users"] = [u for u in users if u]
    if "is_active" in data:
        value = data.get("is_active")
        clean["is_active"] = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "y", "yes", "on")
    return clean


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

        data = {**data, **_clean_form_references(data, organization_id)}
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
        data = {**data, **_clean_form_references(data, organization_id)}
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
            form.notify_users = data["notify_users"]
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
            existing = find_lead_for_form_upsert(form.organization_id, email=lead_payload.get("email"))
            if existing:
                _fill_empty_fields(existing, lead_payload)
                existing.updated_at = _utc_now()
                db.session.flush()
                LeadService.log_activity(
                    existing.id,
                    None,
                    "updated",
                    content=f"Lähetti lomakkeen: {form.name}",
                )
                lead = existing
            else:
                lead, _action = upsert_lead(form.organization_id, lead_payload, match_by_email=False)
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
