from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.tasks.models import Task
from app.webhooks.encryption import decrypt_webhook_secret
from app.webhooks.models import (
    WEBHOOK_DELIVERY_STATUSES,
    WEBHOOK_PROVIDERS,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.webhooks.payloads import WEBHOOK_EVENTS

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 10
WEBHOOK_USER_AGENT = "FlowLeads-Webhooks/1.0"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WebhookService:
    @staticmethod
    def dispatch(event_type, payload, organization_id, triggered_by=None):
        if event_type not in WEBHOOK_EVENTS:
            return {"matched_endpoints": 0, "delivered": 0, "failed": 0}

        endpoints = (
            WebhookEndpoint.query.filter_by(
                organization_id=organization_id,
                is_active=True,
            )
            .order_by(WebhookEndpoint.id.asc())
            .all()
        )
        matched = [
            endpoint for endpoint in endpoints if event_type in (endpoint.events or [])
        ]

        delivered = 0
        failed = 0
        for endpoint in matched:
            try:
                formatted_payload = WebhookService.format_payload(
                    event_type,
                    {
                        "event": event_type,
                        "organization_id": organization_id,
                        "data": payload,
                        "triggered_by": triggered_by,
                        "timestamp": _utc_now().isoformat().replace("+00:00", "Z"),
                    },
                    endpoint.provider,
                )
                delivery = WebhookDelivery(
                    endpoint_id=endpoint.id,
                    organization_id=organization_id,
                    event_type=event_type,
                    payload=formatted_payload,
                    status="pending",
                )
                db.session.add(delivery)
                db.session.flush()

                WebhookService.send_delivery(delivery.id)
                db.session.flush()
                if delivery.status == "delivered":
                    delivered += 1
                else:
                    failed += 1
            except Exception:
                logger.exception(
                    "Webhook dispatch failed for endpoint=%s event=%s",
                    endpoint.id,
                    event_type,
                )
                failed += 1
                db.session.rollback()
                # keep original user flow alive; continue with next endpoint
                continue

        return {
            "matched_endpoints": len(matched),
            "delivered": delivered,
            "failed": failed,
        }

    @staticmethod
    def format_payload(event_type, raw_payload, provider):
        if provider == "slack":
            lead = (raw_payload.get("data") or {}).get("lead") or {}
            contact = (lead.get("name") or "").strip() or "N/A"
            company = (lead.get("company") or "").strip() or "Lead"
            score = lead.get("score")
            source = lead.get("source") or "unknown"
            crm_url = lead.get("crm_url") or "https://app.flowleads.fi"
            text = (
                f"*Uusi liidi: {company}* 🎯\n"
                f"*Kontakti:* {contact}\n"
                f"*Score:* {score if score is not None else '-'}\n"
                f"*Lähde:* {source}"
            )
            return {
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": text},
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {
                                    "type": "plain_text",
                                    "text": "Avaa CRM:ssä",
                                },
                                "url": crm_url,
                            }
                        ],
                    },
                ]
            }

        if provider == "teams":
            lead = (raw_payload.get("data") or {}).get("lead") or {}
            crm_url = lead.get("crm_url") or "https://app.flowleads.fi"
            facts = [
                {"title": "Event", "value": event_type},
                {"title": "Lead", "value": lead.get("name") or lead.get("company") or "-"},
                {"title": "Score", "value": str(lead.get("score") or "-")},
                {"title": "Source", "value": str(lead.get("source") or "-")},
            ]
            return {
                "type": "message",
                "attachments": [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": {
                            "type": "AdaptiveCard",
                            "version": "1.4",
                            "body": [
                                {
                                    "type": "TextBlock",
                                    "size": "Medium",
                                    "weight": "Bolder",
                                    "text": f"FlowLeads event: {event_type}",
                                },
                                {
                                    "type": "FactSet",
                                    "facts": facts,
                                },
                            ],
                            "actions": [
                                {
                                    "type": "Action.OpenUrl",
                                    "title": "Avaa CRM:ssä",
                                    "url": crm_url,
                                }
                            ],
                        },
                    }
                ],
            }

        return {
            "event": event_type,
            "timestamp": raw_payload.get("timestamp"),
            "organization_id": raw_payload.get("organization_id"),
            "data": {
                **(raw_payload.get("data") or {}),
                "triggered_by": raw_payload.get("triggered_by"),
            },
        }

    @staticmethod
    def sign_payload(secret, raw_body):
        digest = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={digest}"

    @staticmethod
    def verify_signature(secret, raw_body, signature):
        expected = WebhookService.sign_payload(secret, raw_body)
        return hmac.compare_digest(expected, signature or "")

    @staticmethod
    def send_delivery(delivery_id):
        delivery = (
            WebhookDelivery.query.filter_by(id=delivery_id)
            .options(joinedload(WebhookDelivery.endpoint))
            .first()
        )
        if not delivery:
            return
        endpoint = delivery.endpoint
        if not endpoint or endpoint.provider not in WEBHOOK_PROVIDERS:
            delivery.status = "failed"
            delivery.response_body = "Endpoint missing or invalid provider."
            return

        try:
            url = decrypt_webhook_secret(endpoint.url_encrypted)
            secret = decrypt_webhook_secret(endpoint.secret_encrypted or "")
        except Exception as exc:
            delivery.status = "failed"
            delivery.response_body = str(exc)
            endpoint.failure_count = (endpoint.failure_count or 0) + 1
            delivery.updated_at = _utc_now()
            return

        raw_body = json.dumps(delivery.payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": WEBHOOK_USER_AGENT,
            "X-FlowLeads-Event": delivery.event_type,
            "X-FlowLeads-Delivery": str(delivery.id),
        }
        if endpoint.provider == "custom" and secret:
            headers["X-FlowLeads-Signature"] = WebhookService.sign_payload(secret, raw_body)

        started = time.perf_counter()
        status_code = None
        body_text = ""
        try:
            response = requests.post(
                url,
                data=raw_body,
                headers=headers,
                timeout=WEBHOOK_TIMEOUT_SECONDS,
            )
            status_code = response.status_code
            body_text = (response.text or "")[:5000]
            if 200 <= response.status_code < 300:
                delivery.status = "delivered"
                delivery.delivered_at = _utc_now()
                endpoint.success_count = (endpoint.success_count or 0) + 1
                endpoint.last_triggered_at = _utc_now()
            else:
                delivery.status = "failed"
                endpoint.failure_count = (endpoint.failure_count or 0) + 1
        except Exception as exc:
            delivery.status = "failed"
            body_text = str(exc)[:5000]
            endpoint.failure_count = (endpoint.failure_count or 0) + 1
        finally:
            delivery.response_status = status_code
            delivery.response_body = body_text
            delivery.duration_ms = int((time.perf_counter() - started) * 1000)
            delivery.updated_at = _utc_now()

    @staticmethod
    def retry_pending_deliveries():
        cutoff = _utc_now() - timedelta(minutes=5)
        deliveries = (
            WebhookDelivery.query.filter(
                WebhookDelivery.status == "failed",
                WebhookDelivery.retry_count == 0,
                WebhookDelivery.created_at <= cutoff,
            )
            .order_by(WebhookDelivery.created_at.asc())
            .all()
        )
        retried = 0
        for delivery in deliveries:
            try:
                delivery.retry_count = 1
                WebhookService.send_delivery(delivery.id)
                retried += 1
            except Exception:
                logger.exception("Webhook retry failed for delivery=%s", delivery.id)
                continue
        return retried

    @staticmethod
    def send_test(endpoint_id, user_id):
        from app.users.models import User

        user = db.session.get(User, user_id)
        if not user or not user.organization_id:
            return {"success": False, "error": "User or organization not found."}

        endpoint = WebhookEndpoint.query.filter_by(
            id=endpoint_id, organization_id=user.organization_id
        ).first()
        if not endpoint:
            return {"success": False, "error": "Endpoint not found."}

        sample_payload = {
            "lead": {
                "id": 123,
                "name": "John Doe",
                "company": "Acme Corp",
                "score": 82,
                "source": "n8n",
                "crm_url": "https://app.flowleads.fi/leads/123",
            }
        }
        payload = WebhookService.format_payload(
            "lead.created",
            {
                "event": "lead.created",
                "organization_id": user.organization_id,
                "data": sample_payload,
                "triggered_by": {"id": user.id},
                "timestamp": _utc_now().isoformat().replace("+00:00", "Z"),
            },
            endpoint.provider,
        )
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            organization_id=user.organization_id,
            event_type="lead.created",
            payload=payload,
            status="pending",
        )
        db.session.add(delivery)
        db.session.flush()
        WebhookService.send_delivery(delivery.id)

        return {
            "success": delivery.status == "delivered",
            "delivery_id": delivery.id,
            "status": delivery.status,
            "response_status": delivery.response_status,
        }

    @staticmethod
    def dispatch_task_overdue_events():
        # Try to avoid duplicates by only sending one overdue event per task per org.
        tasks = Task.query.filter(
            Task.status.in_(("pending", "in_progress")),
            Task.due_date < _utc_now(),
        ).all()
        created = 0
        for task in tasks:
            existing = (
                WebhookDelivery.query.filter_by(
                    organization_id=task.organization_id,
                    event_type="task.overdue",
                )
                .order_by(WebhookDelivery.created_at.desc())
                .limit(200)
                .all()
            )
            already_sent = any(
                (d.payload or {}).get("data", {}).get("task", {}).get("id") == task.id
                for d in existing
            )
            if already_sent:
                continue
            payload = {
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "due_date": task.due_date.isoformat() if task.due_date else None,
                    "lead_id": task.lead_id,
                }
            }
            WebhookService.dispatch(
                "task.overdue",
                payload,
                task.organization_id,
                triggered_by="system",
            )
            created += 1
        return created


assert set(WEBHOOK_DELIVERY_STATUSES) == {"pending", "delivered", "failed"}

