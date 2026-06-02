from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import url_for
from sqlalchemy.orm import joinedload

from app.automations.triggers import fire_automation_trigger
from app.core.audit import log_audit
from app.email.services import EmailService, EmailServiceError
from app.extensions import db
from app.leads.models import ACTIVITY_TYPES, Lead, PipelineStage
from app.leads.services import LeadService, get_lead_for_org
from app.notifications.services import NotificationService
from app.proposals.models import PROPOSAL_STATUSES, Proposal, ProposalLineItem, ProposalTemplate
from app.proposals.settings import (
    get_default_tax_percent,
    get_default_valid_days,
    get_proposal_settings,
)
from app.proposals.utils import generate_reference_number, line_item_total, money
from app.tasks.settings import get_organization_settings
from app.users.models import User

logger = logging.getLogger(__name__)


class ProposalServiceError(Exception):
    def __init__(self, message: str, code: str = "proposal_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_dispatch_webhook(event_type: str, payload: dict, organization_id: int, triggered_by=None) -> None:
    try:
        from app.webhooks.services import WebhookService

        WebhookService.dispatch(
            event_type,
            payload,
            organization_id,
            triggered_by=triggered_by,
        )
    except Exception:
        pass


def get_proposal_for_org(proposal_id: int, organization_id: int) -> Proposal:
    proposal = (
        Proposal.query.filter_by(id=proposal_id, organization_id=organization_id)
        .options(joinedload(Proposal.line_items), joinedload(Proposal.lead))
        .first()
    )
    if not proposal:
        raise ProposalServiceError("Proposal not found.", "not_found")
    return proposal


def get_proposal_by_token(view_token: str) -> Proposal | None:
    if not view_token or len(view_token) < 16:
        return None
    return (
        Proposal.query.filter_by(view_token=view_token)
        .options(joinedload(Proposal.line_items), joinedload(Proposal.lead))
        .first()
    )


def _lead_display_name(lead: Lead) -> str:
    return lead.display_name


def _log_proposal_activity(
    proposal: Proposal,
    user_id: int | None,
    activity_type: str,
    *,
    content: str | None = None,
    metadata: dict | None = None,
) -> None:
    if activity_type not in ACTIVITY_TYPES:
        raise ProposalServiceError("Invalid activity type.", "invalid_activity")
    meta = dict(metadata or {})
    meta["proposal_id"] = proposal.id
    meta["reference_number"] = proposal.reference_number
    LeadService.log_activity(
        proposal.lead_id,
        user_id,
        activity_type,
        content=content,
        metadata=meta,
    )


def _parse_line_items_data(items: list | None) -> list[dict]:
    if not items:
        return []
    parsed = []
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        desc = (raw.get("description") or "").strip()
        if not desc:
            continue
        parsed.append(
            {
                "order_index": int(raw.get("order_index", idx)),
                "description": desc[:500],
                "quantity": raw.get("quantity", 1),
                "unit_price": raw.get("unit_price", 0),
                "discount_percent": raw.get("discount_percent", 0),
            }
        )
    return parsed


class ProposalService:
    @staticmethod
    def list_for_organization(
        organization_id: int,
        *,
        status: str | None = None,
        lead_id: int | None = None,
    ) -> list[Proposal]:
        q = Proposal.query.filter_by(organization_id=organization_id).options(
            joinedload(Proposal.lead)
        )
        if status and status in PROPOSAL_STATUSES:
            q = q.filter_by(status=status)
        if lead_id is not None:
            q = q.filter_by(lead_id=lead_id)
        return q.order_by(Proposal.created_at.desc()).all()

    @staticmethod
    def create(
        lead_id: int,
        data: dict,
        user_id: int | None,
        organization_id: int,
    ) -> Proposal:
        lead = get_lead_for_org(lead_id, organization_id)
        title = (data.get("title") or "").strip()
        if not title:
            title = f"Tarjous — {lead.company or lead.display_name}"

        settings = get_proposal_settings(organization_id)
        valid_days = int(data.get("valid_days") or settings.proposal_default_valid_days or 30)
        valid_until = data.get("valid_until")
        if valid_until is None:
            valid_until = date.today() + timedelta(days=valid_days)
        elif isinstance(valid_until, str):
            valid_until = date.fromisoformat(valid_until[:10])

        tax_percent = data.get("tax_percent")
        if tax_percent is None:
            tax_percent = get_default_tax_percent(organization_id)
        else:
            tax_percent = Decimal(str(tax_percent))

        proposal = Proposal(
            organization_id=organization_id,
            lead_id=lead.id,
            created_by=user_id,
            title=title[:255],
            reference_number=generate_reference_number(organization_id),
            status="draft",
            valid_until=valid_until,
            currency=(data.get("currency") or "EUR")[:3],
            discount_percent=Decimal(str(data.get("discount_percent") or 0)),
            tax_percent=tax_percent,
            notes=(data.get("notes") or settings.proposal_default_notes or "").strip() or None,
            lead_name_snapshot=_lead_display_name(lead),
            lead_company_snapshot=lead.company,
            lead_email_snapshot=lead.email,
            is_locked=False,
        )
        db.session.add(proposal)
        db.session.flush()

        line_data = _parse_line_items_data(data.get("line_items"))
        if not line_data:
            line_data = [
                {
                    "order_index": 0,
                    "description": "Palvelu",
                    "quantity": 1,
                    "unit_price": 0,
                    "discount_percent": 0,
                }
            ]

        for item in line_data:
            line = ProposalLineItem(
                proposal_id=proposal.id,
                order_index=item["order_index"],
                description=item["description"],
                quantity=Decimal(str(item["quantity"])),
                unit_price=Decimal(str(item["unit_price"])),
                discount_percent=Decimal(str(item["discount_percent"] or 0)),
            )
            line.total = line_item_total(line.quantity, line.unit_price, line.discount_percent)
            db.session.add(line)

        db.session.flush()
        ProposalService.calculate_totals(proposal)
        return proposal

    @staticmethod
    def calculate_totals(proposal: Proposal) -> Proposal:
        subtotal = Decimal("0")
        for line in proposal.line_items:
            line.total = line_item_total(line.quantity, line.unit_price, line.discount_percent)
            subtotal += line.total

        proposal.subtotal = money(subtotal)
        disc_pct = Decimal(str(proposal.discount_percent or 0))
        if disc_pct > 0:
            proposal.discount_amount = money(proposal.subtotal * disc_pct / Decimal("100"))
        else:
            proposal.discount_amount = money(proposal.discount_amount or 0)

        taxable = proposal.subtotal - proposal.discount_amount
        if taxable < 0:
            taxable = Decimal("0")
        tax_pct = Decimal(str(proposal.tax_percent or 0))
        tax_amount = money(taxable * tax_pct / Decimal("100")) if tax_pct > 0 else Decimal("0")
        proposal.total = money(taxable + tax_amount)
        proposal.updated_at = _utc_now()
        db.session.flush()
        return proposal

    @staticmethod
    def update(proposal_id: int, data: dict, organization_id: int, user_id: int | None) -> Proposal:
        proposal = get_proposal_for_org(proposal_id, organization_id)
        if not proposal.is_editable:
            raise ProposalServiceError(
                "Proposal is locked and cannot be edited. Duplicate to create a new draft.",
                "locked",
            )

        if "title" in data:
            title = (data.get("title") or "").strip()
            if title:
                proposal.title = title[:255]
        if "valid_until" in data:
            vu = data["valid_until"]
            if isinstance(vu, str):
                proposal.valid_until = date.fromisoformat(vu[:10]) if vu else None
            elif vu is None or isinstance(vu, date):
                proposal.valid_until = vu
        if "notes" in data:
            proposal.notes = (data.get("notes") or "").strip() or None
        if "currency" in data:
            proposal.currency = (data.get("currency") or "EUR")[:3]
        if "discount_percent" in data:
            proposal.discount_percent = Decimal(str(data.get("discount_percent") or 0))
        if "discount_amount" in data and not data.get("discount_percent"):
            proposal.discount_amount = money(data.get("discount_amount") or 0)
            proposal.discount_percent = Decimal("0")
        if "tax_percent" in data:
            proposal.tax_percent = Decimal(str(data.get("tax_percent") or 0))

        if "line_items" in data:
            ProposalLineItem.query.filter_by(proposal_id=proposal.id).delete()
            db.session.flush()
            for idx, item in enumerate(_parse_line_items_data(data["line_items"])):
                line = ProposalLineItem(
                    proposal_id=proposal.id,
                    order_index=item["order_index"] if item.get("order_index") is not None else idx,
                    description=item["description"],
                    quantity=Decimal(str(item["quantity"])),
                    unit_price=Decimal(str(item["unit_price"])),
                    discount_percent=Decimal(str(item["discount_percent"] or 0)),
                )
                line.total = line_item_total(line.quantity, line.unit_price, line.discount_percent)
                db.session.add(line)
            db.session.flush()

        proposal.updated_at = _utc_now()
        ProposalService.calculate_totals(proposal)
        return proposal

    @staticmethod
    def delete(proposal_id: int, organization_id: int) -> None:
        proposal = get_proposal_for_org(proposal_id, organization_id)
        if proposal.status != "draft":
            raise ProposalServiceError("Only draft proposals can be deleted.", "locked")
        db.session.delete(proposal)

    @staticmethod
    def send(proposal_id: int, user_id: int | None, organization_id: int) -> str:
        proposal = get_proposal_for_org(proposal_id, organization_id)
        if proposal.status != "draft":
            raise ProposalServiceError("Only draft proposals can be sent.", "invalid_status")
        if proposal.is_locked:
            raise ProposalServiceError("Proposal is already locked.", "locked")

        lead = get_lead_for_org(proposal.lead_id, organization_id)
        if not lead.email:
            raise ProposalServiceError("Lead has no email address.", "no_email")

        token = secrets.token_urlsafe(32)
        proposal.view_token = token
        proposal.status = "sent"
        proposal.sent_at = _utc_now()
        proposal.is_locked = True
        proposal.updated_at = _utc_now()
        db.session.flush()

        public_url = ProposalService._public_url(token)
        ProposalService._send_proposal_email(proposal, lead, public_url, user_id)

        _log_proposal_activity(
            proposal,
            user_id,
            "proposal_sent",
            content=proposal.title,
            metadata={"public_url": public_url},
        )
        return public_url

    @staticmethod
    def _public_url(view_token: str) -> str:
        try:
            return url_for("proposals_public.view_proposal", view_token=view_token, _external=True)
        except RuntimeError:
            return f"/p/{view_token}"

    @staticmethod
    def _send_proposal_email(
        proposal: Proposal,
        lead: Lead,
        public_url: str,
        user_id: int | None,
    ) -> None:
        from app.email.templates import render_template_text
        from app.email.models import EmailTemplate

        template = EmailTemplate.query.filter_by(
            organization_id=None, name="proposal_sent"
        ).first()
        if not template:
            subject = f"Tarjous {proposal.reference_number} — {proposal.title}"
            body_html = (
                f"<p>Hei {proposal.lead_name_snapshot or lead.display_name},</p>"
                f"<p>Lähetämme tarjouksen <strong>{proposal.reference_number}</strong>.</p>"
                f"<p><a href=\"{public_url}\">Avaa tarjous</a></p>"
                f"<p>Voimassa: {proposal.valid_until}</p>"
            )
            body_text = (
                f"Hei,\n\nTarjous {proposal.reference_number}.\n"
                f"Avaa: {public_url}\nVoimassa: {proposal.valid_until}\n"
            )
        else:
            ctx = {
                "first_name": (lead.first_name or proposal.lead_name_snapshot or "asiakas"),
                "company": proposal.lead_company_snapshot or lead.company or "",
                "reference_number": proposal.reference_number,
                "proposal_title": proposal.title,
                "proposal_url": public_url,
                "valid_until": str(proposal.valid_until or ""),
                "total": str(proposal.total),
            }
            subject = render_template_text(template.subject_template, ctx)
            body_html = render_template_text(template.body_html_template, ctx)
            body_text = render_template_text(template.body_text_template or "", ctx)

        try:
            EmailService.send_to_lead(
                lead.id,
                user_id,
                subject,
                body_html,
                body_text,
                organization_id=proposal.organization_id,
            )
        except EmailServiceError as exc:
            logger.warning("Proposal email failed: %s", exc.message)
            if exc.code == "sending_disabled":
                pass
            else:
                raise ProposalServiceError(exc.message, exc.code) from exc

    @staticmethod
    def record_view(view_token: str, request_meta: dict) -> dict:
        proposal = get_proposal_by_token(view_token)
        if not proposal:
            raise ProposalServiceError("Proposal not found.", "not_found")

        if proposal.status == "expired" or (
            proposal.valid_until and proposal.valid_until < date.today() and proposal.status in ("sent", "viewed")
        ):
            if proposal.status != "expired":
                proposal.status = "expired"
                db.session.flush()
            return {"state": "expired", "proposal": proposal}

        if proposal.status in ("accepted", "declined"):
            return {"state": proposal.status, "proposal": proposal}

        proposal.opened_count = (proposal.opened_count or 0) + 1
        proposal.last_opened_at = _utc_now()

        first_view = proposal.status == "sent"
        if first_view:
            proposal.status = "viewed"
            proposal.viewed_at = _utc_now()
            _log_proposal_activity(
                proposal,
                None,
                "proposal_viewed",
                content=proposal.title,
            )
            fire_automation_trigger(
                "proposal_viewed",
                {
                    "lead_id": proposal.lead_id,
                    "proposal_id": proposal.id,
                    "reference_number": proposal.reference_number,
                },
                proposal.organization_id,
            )
            _safe_dispatch_webhook(
                "proposal.viewed",
                {
                    "proposal": {
                        "id": proposal.id,
                        "reference_number": proposal.reference_number,
                    },
                    "lead": {"id": proposal.lead_id},
                },
                proposal.organization_id,
                triggered_by="system",
            )

        proposal.updated_at = _utc_now()
        db.session.flush()
        return {"state": "ok", "proposal": proposal, "first_view": first_view}

    @staticmethod
    def accept(
        view_token: str,
        signature_name: str,
        request_meta: dict,
    ) -> Proposal:
        proposal = get_proposal_by_token(view_token)
        if not proposal:
            raise ProposalServiceError("Proposal not found.", "not_found")

        if proposal.status == "accepted":
            raise ProposalServiceError("Proposal already accepted.", "already_accepted")
        if proposal.status in ("declined", "expired"):
            raise ProposalServiceError("Proposal cannot be accepted.", "invalid_status")
        if proposal.valid_until and proposal.valid_until < date.today():
            proposal.status = "expired"
            db.session.flush()
            raise ProposalServiceError("Proposal has expired.", "expired")

        name = (signature_name or "").strip()
        if not name:
            raise ProposalServiceError("Signature name is required.", "validation_error")

        now = _utc_now()
        proposal.status = "accepted"
        proposal.accepted_at = now
        proposal.signed_at = now
        proposal.signature_name = name[:255]
        proposal.signature_ip = (request_meta.get("ip") or "")[:64] or None
        proposal.signature_user_agent = (request_meta.get("user_agent") or "")[:500] or None
        proposal.updated_at = now
        db.session.flush()

        _log_proposal_activity(
            proposal,
            None,
            "proposal_accepted",
            content=f"Accepted by {name}",
            metadata={"signature_name": name},
        )
        log_audit(
            "proposal_accepted",
            organization_id=proposal.organization_id,
            target_type="proposal",
            target_id=proposal.id,
            metadata={"lead_id": proposal.lead_id, "reference_number": proposal.reference_number},
        )
        fire_automation_trigger(
            "proposal_accepted",
            {
                "lead_id": proposal.lead_id,
                "proposal_id": proposal.id,
                "reference_number": proposal.reference_number,
                "total": str(proposal.total),
            },
            proposal.organization_id,
        )
        _safe_dispatch_webhook(
            "proposal.accepted",
            {
                "proposal": {
                    "id": proposal.id,
                    "reference_number": proposal.reference_number,
                    "total": str(proposal.total),
                },
                "lead": {"id": proposal.lead_id},
            },
            proposal.organization_id,
            triggered_by="system",
        )

        ProposalService._move_lead_to_won_if_enabled(proposal)
        ProposalService._notify_assignee_accepted(proposal)
        return proposal

    @staticmethod
    def _move_lead_to_won_if_enabled(proposal: Proposal) -> None:
        settings = get_organization_settings(proposal.organization_id)
        if not settings.proposal_move_lead_to_won_on_accept:
            return
        won_stage = PipelineStage.query.filter_by(
            organization_id=proposal.organization_id, name="Voitettu"
        ).first()
        if not won_stage:
            won_stage = PipelineStage.query.filter_by(
                organization_id=proposal.organization_id, name="Won"
            ).first()
        if not won_stage:
            return
        try:
            LeadService.move_stage(
                proposal.lead_id, won_stage.id, proposal.organization_id, None
            )
        except Exception:
            logger.exception("Failed to move lead to Won after proposal accept")

    @staticmethod
    def _notify_assignee_accepted(proposal: Proposal) -> None:
        lead = proposal.lead or get_lead_for_org(proposal.lead_id, proposal.organization_id)
        if not lead.assigned_to:
            return
        link = f"/leads/{lead.id}"
        NotificationService.create(
            user_id=lead.assigned_to,
            organization_id=proposal.organization_id,
            type="proposal_accepted",
            title=f"Tarjous hyväksytty: {proposal.reference_number}",
            message=f"{proposal.lead_name_snapshot or lead.display_name} hyväksyi tarjouksen.",
            link=link,
        )
        ProposalService._send_accept_notification_email(proposal, lead)

    @staticmethod
    def _send_accept_notification_email(proposal: Proposal, lead: Lead) -> None:
        if not lead.assigned_to:
            return
        assignee = db.session.get(User, lead.assigned_to)
        if not assignee or not assignee.email:
            return
        from app.email.templates import render_template_text
        from app.email.models import EmailTemplate

        template = EmailTemplate.query.filter_by(
            organization_id=None, name="proposal_accepted_notification"
        ).first()
        subject = f"Tarjous hyväksytty: {proposal.reference_number}"
        body_html = (
            f"<p>Tarjous <strong>{proposal.reference_number}</strong> hyväksyttiin.</p>"
            f"<p>Liidi: {proposal.lead_name_snapshot}</p>"
            f"<p>Summa: {proposal.total} {proposal.currency}</p>"
        )
        body_text = f"Tarjous {proposal.reference_number} hyväksytty. Summa: {proposal.total}"
        if template:
            ctx = {
                "reference_number": proposal.reference_number,
                "lead_name": proposal.lead_name_snapshot or "",
                "total": str(proposal.total),
                "currency": proposal.currency,
            }
            subject = render_template_text(template.subject_template, ctx)
            body_html = render_template_text(template.body_html_template, ctx)
            body_text = render_template_text(template.body_text_template or "", ctx)

        from flask import current_app
        from flask_mail import Message

        if not current_app.config.get("EMAIL_SENDING_ENABLED"):
            return
        try:
            msg = Message(subject=subject, recipients=[assignee.email], html=body_html, body=body_text)
            from app.extensions import mail

            mail.send(msg)
        except Exception:
            logger.exception("Failed to send proposal accepted notification email")

    @staticmethod
    def decline(view_token: str, reason: str | None = None) -> Proposal:
        proposal = get_proposal_by_token(view_token)
        if not proposal:
            raise ProposalServiceError("Proposal not found.", "not_found")
        if proposal.status in ("accepted", "declined"):
            raise ProposalServiceError("Proposal cannot be declined.", "invalid_status")

        proposal.status = "declined"
        proposal.declined_at = _utc_now()
        proposal.updated_at = _utc_now()
        db.session.flush()

        content = (reason or "").strip() or "Declined"
        _log_proposal_activity(
            proposal,
            None,
            "proposal_declined",
            content=content[:500],
        )
        fire_automation_trigger(
            "proposal_declined",
            {
                "lead_id": proposal.lead_id,
                "proposal_id": proposal.id,
                "reference_number": proposal.reference_number,
                "reason": content,
            },
            proposal.organization_id,
        )
        _safe_dispatch_webhook(
            "proposal.declined",
            {
                "proposal": {
                    "id": proposal.id,
                    "reference_number": proposal.reference_number,
                    "reason": content,
                },
                "lead": {"id": proposal.lead_id},
            },
            proposal.organization_id,
            triggered_by="system",
        )
        ProposalService._notify_assignee_declined(proposal, content)
        return proposal

    @staticmethod
    def _notify_assignee_declined(proposal: Proposal, reason: str) -> None:
        lead = proposal.lead or get_lead_for_org(proposal.lead_id, proposal.organization_id)
        if not lead.assigned_to:
            return
        NotificationService.create(
            user_id=lead.assigned_to,
            organization_id=proposal.organization_id,
            type="proposal_declined",
            title=f"Tarjous hylätty: {proposal.reference_number}",
            message=reason[:500],
            link=f"/leads/{lead.id}",
        )
        assignee = db.session.get(User, lead.assigned_to)
        if assignee and assignee.email:
            from flask import current_app
            from flask_mail import Message

            if current_app.config.get("EMAIL_SENDING_ENABLED"):
                try:
                    msg = Message(
                        subject=f"Tarjous hylätty: {proposal.reference_number}",
                        recipients=[assignee.email],
                        body=f"Tarjous {proposal.reference_number} hylättiin.\n{reason}",
                    )
                    from app.extensions import mail

                    mail.send(msg)
                except Exception:
                    logger.exception("Failed to send proposal declined notification email")

    @staticmethod
    def duplicate(proposal_id: int, organization_id: int, user_id: int | None) -> Proposal:
        source = get_proposal_for_org(proposal_id, organization_id)
        data = {
            "title": f"{source.title} (kopio)",
            "valid_until": source.valid_until.isoformat() if source.valid_until else None,
            "currency": source.currency,
            "discount_percent": float(source.discount_percent),
            "tax_percent": float(source.tax_percent),
            "notes": source.notes,
            "line_items": [
                {
                    "order_index": li.order_index,
                    "description": li.description,
                    "quantity": float(li.quantity),
                    "unit_price": float(li.unit_price),
                    "discount_percent": float(li.discount_percent),
                }
                for li in sorted(source.line_items, key=lambda x: x.order_index)
            ],
        }
        return ProposalService.create(source.lead_id, data, user_id, organization_id)

    @staticmethod
    def expire_old_proposals() -> int:
        today = date.today()
        rows = Proposal.query.filter(
            Proposal.status.in_(("sent", "viewed")),
            Proposal.valid_until.isnot(None),
            Proposal.valid_until < today,
        ).all()
        count = 0
        for proposal in rows:
            proposal.status = "expired"
            proposal.updated_at = _utc_now()
            count += 1
            fire_automation_trigger(
                "proposal_expired",
                {
                    "lead_id": proposal.lead_id,
                    "proposal_id": proposal.id,
                    "reference_number": proposal.reference_number,
                },
                proposal.organization_id,
            )
        if count:
            db.session.flush()
        return count

    @staticmethod
    def get_open_count(organization_id: int) -> int:
        return Proposal.query.filter(
            Proposal.organization_id == organization_id,
            Proposal.status.in_(("sent", "viewed")),
        ).count()

    @staticmethod
    def get_accepted_this_month_total(organization_id: int) -> Decimal:
        month_start = _utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = Proposal.query.filter(
            Proposal.organization_id == organization_id,
            Proposal.status == "accepted",
            Proposal.accepted_at.isnot(None),
            Proposal.accepted_at >= month_start,
        ).all()
        return money(sum((r.total for r in rows), Decimal("0")))

    @staticmethod
    def get_lead_proposals_summary(lead_id: int, organization_id: int) -> dict:
        proposals = ProposalService.list_for_organization(organization_id, lead_id=lead_id)
        accepted_total = money(
            sum((p.total for p in proposals if p.status == "accepted"), Decimal("0"))
        )
        return {"proposals": proposals, "accepted_total": accepted_total}

    @staticmethod
    def get_template(organization_id: int) -> ProposalTemplate | None:
        return (
            ProposalTemplate.query.filter_by(organization_id=organization_id)
            .order_by(ProposalTemplate.created_at.desc())
            .first()
        )

    @staticmethod
    def save_template(organization_id: int, data: dict, user_id: int | None) -> ProposalTemplate:
        template = ProposalService.get_template(organization_id)
        if not template:
            template = ProposalTemplate(
                organization_id=organization_id,
                name=(data.get("name") or "Oletus").strip()[:200],
                created_by=user_id,
            )
            db.session.add(template)
        template.default_valid_days = int(data.get("default_valid_days") or 30)
        template.default_notes = (data.get("default_notes") or "").strip() or None
        template.default_tax_percent = Decimal(str(data.get("default_tax_percent") or 24))
        template.header_html = (data.get("header_html") or "").strip() or None
        template.footer_html = (data.get("footer_html") or "").strip() or None
        if "name" in data and data.get("name"):
            template.name = data["name"].strip()[:200]
        db.session.flush()
        return template

    @staticmethod
    def save_org_settings(organization_id: int, data: dict) -> None:
        settings = get_organization_settings(organization_id)
        if "proposal_move_lead_to_won_on_accept" in data:
            settings.proposal_move_lead_to_won_on_accept = bool(
                data["proposal_move_lead_to_won_on_accept"]
            )
        if "proposal_default_valid_days" in data:
            settings.proposal_default_valid_days = int(data["proposal_default_valid_days"] or 30)
        if "proposal_default_tax_percent" in data:
            settings.proposal_default_tax_percent = Decimal(
                str(data["proposal_default_tax_percent"] or 24)
            )
        if "proposal_default_notes" in data:
            settings.proposal_default_notes = (data.get("proposal_default_notes") or "").strip() or None
        db.session.flush()

    @staticmethod
    def public_proposal_dict(proposal: Proposal) -> dict:
        """Safe subset for public pages — no organization_id or internal ids in URLs."""
        lines = [
            {
                "description": li.description,
                "quantity": str(li.quantity),
                "unit_price": str(li.unit_price),
                "discount_percent": str(li.discount_percent),
                "total": str(li.total),
            }
            for li in sorted(proposal.line_items, key=lambda x: x.order_index)
        ]
        return {
            "reference_number": proposal.reference_number,
            "title": proposal.title,
            "status": proposal.status,
            "valid_until": proposal.valid_until.isoformat() if proposal.valid_until else None,
            "currency": proposal.currency,
            "subtotal": str(proposal.subtotal),
            "discount_percent": str(proposal.discount_percent),
            "discount_amount": str(proposal.discount_amount),
            "tax_percent": str(proposal.tax_percent),
            "total": str(proposal.total),
            "notes": proposal.notes,
            "lead_name": proposal.lead_name_snapshot,
            "lead_company": proposal.lead_company_snapshot,
            "line_items": lines,
            "signature_name": proposal.signature_name,
            "accepted_at": proposal.accepted_at.isoformat() if proposal.accepted_at else None,
            "is_expired": proposal.is_expired or proposal.status == "expired",
        }
