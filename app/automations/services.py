from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload

from app.automations.constants import ACTION_TYPES, LOG_RESULTS, TRIGGER_TYPES
from app.automations.encryption import decrypt_webhook_headers, encrypt_webhook_headers
from app.automations.models import Automation, AutomationAction, AutomationLog
from app.extensions import db
from app.leads.models import Activity, Lead, PipelineStage
from app.leads.services import LeadService
from app.leads.validators import normalize_tags
from app.users.models import User

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 10
TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


class AutomationServiceError(Exception):
    def __init__(self, message: str, code: str = "automation_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_automation_for_org(automation_id: int, organization_id: int) -> Automation:
    automation = (
        Automation.query.filter_by(id=automation_id, organization_id=organization_id)
        .options(joinedload(Automation.actions))
        .first()
    )
    if not automation:
        raise AutomationServiceError("Automation not found.", "not_found")
    return automation


def render_template_string(template: str, lead: Lead, context: dict | None = None) -> str:
    context = context or {}
    values = {
        "lead.id": str(lead.id),
        "lead.company": lead.company or "",
        "lead.email": lead.email or "",
        "lead.first_name": lead.first_name or "",
        "lead.last_name": lead.last_name or "",
        "lead.display_name": lead.display_name,
        "lead.score": str(lead.score) if lead.score is not None else "",
        "lead.source": lead.source or "",
    }
    values.update({k: str(v) for k, v in context.items() if v is not None})

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        return values.get(key, match.group(0))

    return TEMPLATE_VAR_RE.sub(replacer, template or "")


def evaluate_trigger_conditions(
    trigger_type: str,
    trigger_config: dict | None,
    payload: dict,
    lead: Lead | None,
) -> bool:
    config = trigger_config or {}

    sources = config.get("source")
    if sources is not None:
        if not isinstance(sources, list):
            sources = [sources]
        if lead is None or lead.source not in sources:
            return False

    min_score = config.get("min_score")
    if min_score is not None and lead is not None:
        if lead.score is None or lead.score < int(min_score):
            return False

    if trigger_type == "lead_stage_changed":
        to_stage = config.get("to_stage_id")
        if to_stage is not None and payload.get("new_stage_id") != int(to_stage):
            return False
        from_stage = config.get("from_stage_id")
        if from_stage is not None and payload.get("old_stage_id") != int(from_stage):
            return False

    if trigger_type == "lead_score_changed":
        threshold = int(config.get("threshold", config.get("min_score", 80)))
        operator = config.get("operator", "crosses_above")
        old_score = payload.get("old_score")
        new_score = payload.get("new_score")
        if new_score is None and lead is not None:
            new_score = lead.score
        if operator == "crosses_above":
            if new_score is None or int(new_score) < threshold:
                return False
            if old_score is not None and int(old_score) >= threshold:
                return False
        elif operator == "gte":
            if new_score is None or int(new_score) < threshold:
                return False
        elif operator == "gt":
            if new_score is None or int(new_score) <= threshold:
                return False

    if trigger_type == "lead_no_activity":
        required_days = int(config.get("days", 14))
        inactive_days = int(payload.get("inactive_days", 0))
        if inactive_days < required_days:
            return False
        stages = config.get("stages")
        if stages and lead is not None and lead.stage_id not in stages:
            return False

    if trigger_type == "lead_tag_added":
        tag = config.get("tag")
        if tag:
            added = payload.get("added_tags") or []
            if tag not in added:
                return False

    if trigger_type == "task_overdue":
        required_hours = int(config.get("hours", 0))
        overdue_hours = float(payload.get("overdue_hours", 0))
        if overdue_hours < required_hours:
            return False

    if trigger_type == "email_opened":
        template_id = config.get("template_id")
        if template_id is not None and payload.get("template_id") != template_id:
            return False

    if trigger_type == "sequence_completed":
        sequence_id = config.get("sequence_id")
        if sequence_id is not None and payload.get("sequence_id") != int(sequence_id):
            return False

    return True


def _prepare_webhook_action_config(action_config: dict) -> dict:
    """Strip plaintext secrets from headers; store encrypted_headers."""
    config = dict(action_config or {})
    headers = config.pop("headers", None) or {}
    secret_headers = config.pop("secret_headers", None) or {}
    if isinstance(secret_headers, dict) and secret_headers:
        encrypted = encrypt_webhook_headers(secret_headers)
        if encrypted:
            config["encrypted_headers"] = encrypted
    plain_headers = {k: v for k, v in (headers or {}).items() if k not in (secret_headers or {})}
    if plain_headers:
        config["headers"] = plain_headers
    return config


class AutomationEngine:
    @staticmethod
    def trigger(event_type: str, payload: dict, organization_id: int) -> None:
        if event_type not in TRIGGER_TYPES:
            return

        payload = dict(payload or {})
        lead_id = payload.get("lead_id")
        lead = None
        if lead_id:
            lead = Lead.query.filter_by(id=lead_id, organization_id=organization_id).first()

        automations = (
            Automation.query.filter_by(
                organization_id=organization_id,
                trigger_type=event_type,
                is_active=True,
            )
            .options(joinedload(Automation.actions))
            .order_by(Automation.id.asc())
            .all()
        )

        for automation in automations:
            try:
                if not evaluate_trigger_conditions(
                    event_type, automation.trigger_config, payload, lead
                ):
                    continue

                context = dict(payload)
                any_failed = False
                last_error = None

                for action in sorted(automation.actions, key=lambda a: a.order_index):
                    ok, err = AutomationEngine.execute_action(
                        action, lead, context, organization_id=organization_id
                    )
                    if not ok:
                        any_failed = True
                        last_error = err

                automation.run_count = (automation.run_count or 0) + 1
                automation.last_run_at = _utc_now()
                AutomationEngine._log_run(
                    automation,
                    lead_id=lead_id,
                    organization_id=organization_id,
                    trigger_data=payload,
                    result="failed" if any_failed else "success",
                    error_message=last_error,
                )
            except Exception as exc:
                logger.exception(
                    "Automation %s execution error", automation.id
                )
                AutomationEngine._log_run(
                    automation,
                    lead_id=lead_id,
                    organization_id=organization_id,
                    trigger_data=payload,
                    result="failed",
                    error_message=str(exc)[:2000],
                )

        try:
            db.session.flush()
        except Exception:
            logger.exception("Failed to flush automation logs")
            db.session.rollback()

    @staticmethod
    def _log_run(
        automation: Automation,
        *,
        lead_id: int | None,
        organization_id: int,
        trigger_data: dict,
        result: str,
        error_message: str | None = None,
    ) -> None:
        if result not in LOG_RESULTS:
            result = "failed"
        log = AutomationLog(
            automation_id=automation.id,
            lead_id=lead_id,
            organization_id=organization_id,
            trigger_data=trigger_data,
            result=result,
            error_message=error_message,
        )
        db.session.add(log)

    @staticmethod
    def execute_action(
        action: AutomationAction,
        lead: Lead | None,
        context: dict,
        *,
        organization_id: int,
    ) -> tuple[bool, str | None]:
        if action.action_type not in ACTION_TYPES:
            return False, f"Unknown action type: {action.action_type}"
        if lead is None and action.action_type not in ("notify_user", "send_webhook"):
            return False, "Lead is required for this action."

        config = action.action_config or {}
        try:
            handler = getattr(AutomationEngine, f"_action_{action.action_type}")
            return handler(lead, config, context, organization_id=organization_id)
        except Exception as exc:
            logger.exception("Action %s failed", action.action_type)
            return False, str(exc)[:2000]

    @staticmethod
    def _resolve_assignee(lead: Lead, config: dict, organization_id: int) -> int | None:
        assign_to = config.get("assign_to", "owner")
        if assign_to == "owner":
            return lead.assigned_to
        if assign_to == "creator":
            return config.get("user_id")
        if assign_to in ("user", "user_id") and config.get("user_id"):
            return int(config["user_id"])
        return lead.assigned_to

    @staticmethod
    def _action_create_task(
        lead: Lead, config: dict, context: dict, *, organization_id: int
    ) -> tuple[bool, str | None]:
        from app.tasks.services import TaskService

        assignee = AutomationEngine._resolve_assignee(lead, config, organization_id)
        if not assignee:
            admin = (
                User.query.filter_by(
                    organization_id=organization_id, role="admin", is_active=True
                )
                .order_by(User.id.asc())
                .first()
            )
            assignee = admin.id if admin else None
        if not assignee:
            return False, "No assignee for task."

        due_days = int(config.get("due_days", 1))
        due_date = _utc_now() + timedelta(days=due_days)
        TaskService.create(
            {
                "title": config.get("title", "Automaatiotehtävä"),
                "type": config.get("type", "follow_up"),
                "priority": config.get("priority", "normal"),
                "due_date": due_date,
                "assigned_to": assignee,
                "description": config.get("description"),
            },
            organization_id,
            None,
            lead_id=lead.id,
        )
        return True, None

    @staticmethod
    def _action_send_email(
        lead: Lead, config: dict, context: dict, *, organization_id: int
    ) -> tuple[bool, str | None]:
        from app.email.models import EmailTemplate
        from app.email.services import EmailService, EmailServiceError
        from app.email.templates import build_template_context, render_template_text

        subject = config.get("subject", "")
        body_html = config.get("body_html")
        body_text = config.get("body_text")
        template_id = config.get("template_id")
        if template_id:
            template = EmailTemplate.query.filter_by(
                id=int(template_id), organization_id=organization_id
            ).first()
            if not template:
                return False, "Email template not found."
            ctx = build_template_context(lead)
            subject = render_template_text(template.subject, ctx)
            body_html = render_template_text(template.body_html or "", ctx)
            body_text = render_template_text(template.body_text or "", ctx)

        try:
            EmailService.send_to_lead(
                lead.id,
                None,
                subject,
                body_html,
                body_text,
                organization_id=organization_id,
            )
            return True, None
        except EmailServiceError as exc:
            return False, exc.message

    @staticmethod
    def _action_enroll_in_sequence(
        lead: Lead, config: dict, context: dict, *, organization_id: int
    ) -> tuple[bool, str | None]:
        from app.sequences.models import EmailSequence
        from app.sequences.services import SequenceService, SequenceServiceError

        sequence_id = config.get("sequence_id")
        if not sequence_id:
            name = config.get("sequence_name")
            if name:
                seq = EmailSequence.query.filter_by(
                    organization_id=organization_id, name=name
                ).first()
                sequence_id = seq.id if seq else None
        if not sequence_id:
            return False, "sequence_id is required."
        try:
            SequenceService.enroll(int(sequence_id), lead.id, organization_id=organization_id)
            return True, None
        except SequenceServiceError as exc:
            return False, exc.message

    @staticmethod
    def _action_change_stage(
        lead: Lead, config: dict, context: dict, *, organization_id: int
    ) -> tuple[bool, str | None]:
        stage_id = config.get("stage_id")
        if not stage_id and config.get("stage_name"):
            stage = PipelineStage.query.filter_by(
                organization_id=organization_id, name=config["stage_name"]
            ).first()
            stage_id = stage.id if stage else None
        if not stage_id:
            return False, "stage_id is required."
        LeadService.move_stage(lead.id, int(stage_id), organization_id, None)
        return True, None

    @staticmethod
    def _action_assign_lead(
        lead: Lead, config: dict, context: dict, *, organization_id: int
    ) -> tuple[bool, str | None]:
        user_id = AutomationEngine._resolve_assignee(lead, config, organization_id)
        if not user_id:
            return False, "user_id is required."
        lead.assigned_to = int(user_id)
        db.session.flush()
        LeadService.log_activity(
            lead.id, None, "assigned", metadata={"assigned_to": user_id, "via": "automation"}
        )
        return True, None

    @staticmethod
    def _action_add_tag(
        lead: Lead, config: dict, context: dict, *, organization_id: int
    ) -> tuple[bool, str | None]:
        tag = (config.get("tag") or "").strip()
        if not tag:
            return False, "tag is required."
        tags = list(lead.tags or [])
        if tag not in tags:
            tags.append(tag)
            lead.tags = normalize_tags(tags)
            db.session.flush()
        return True, None

    @staticmethod
    def _action_remove_tag(
        lead: Lead, config: dict, context: dict, *, organization_id: int
    ) -> tuple[bool, str | None]:
        tag = (config.get("tag") or "").strip()
        if not tag:
            return False, "tag is required."
        tags = [t for t in (lead.tags or []) if t != tag]
        lead.tags = normalize_tags(tags)
        db.session.flush()
        return True, None

    @staticmethod
    def _action_send_webhook(
        lead: Lead | None, config: dict, context: dict, *, organization_id: int
    ) -> tuple[bool, str | None]:
        url = (config.get("url") or "").strip()
        if not url:
            return False, "url is required."
        method = (config.get("method") or "POST").upper()
        headers = dict(config.get("headers") or {})
        headers.update(decrypt_webhook_headers(config.get("encrypted_headers")))
        body_template = config.get("body_template", "{}")
        if lead:
            body_str = render_template_string(body_template, lead, context)
        else:
            body_str = body_template

        data = body_str.encode("utf-8") if body_str else None
        if headers.get("Content-Type") is None and data:
            headers["Content-Type"] = "application/json"
            try:
                json.loads(body_str)
            except json.JSONDecodeError:
                headers["Content-Type"] = "text/plain"

        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_SECONDS) as resp:
                if resp.status >= 400:
                    return False, f"Webhook HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            return False, f"Webhook HTTP {exc.code}"
        except Exception as exc:
            return False, str(exc)[:500]
        return True, None

    @staticmethod
    def _action_notify_user(
        lead: Lead | None, config: dict, context: dict, *, organization_id: int
    ) -> tuple[bool, str | None]:
        from app.notifications.services import NotificationService, NotificationServiceError

        user_id = config.get("user_id")
        if config.get("notify") == "owner" and lead:
            user_id = lead.assigned_to
        if not user_id:
            return False, "user_id is required."
        title = config.get("title", "Automaatioilmoitus")
        message = config.get("message", "")
        if lead and "{{" in message:
            message = render_template_string(message, lead, context)
        link = config.get("link")
        if lead and not link:
            link = f"/leads/{lead.id}"
        try:
            NotificationService.create(
                user_id=int(user_id),
                organization_id=organization_id,
                type=config.get("type", "automation"),
                title=title,
                message=message or title,
                link=link,
            )
            return True, None
        except NotificationServiceError as exc:
            return False, exc.message

    @staticmethod
    def run_no_activity_checks() -> int:
        """Daily job: fire lead_no_activity for eligible leads."""
        triggered = 0
        now = _utc_now()
        org_ids = db.session.query(Lead.organization_id).distinct().all()
        for (org_id,) in org_ids:
            automations = Automation.query.filter_by(
                organization_id=org_id,
                trigger_type="lead_no_activity",
                is_active=True,
            ).count()
            if not automations:
                continue

            leads = Lead.query.filter_by(organization_id=org_id, status="active").all()
            for lead in leads:
                last_activity = (
                    Activity.query.filter_by(lead_id=lead.id, organization_id=org_id)
                    .order_by(Activity.created_at.desc())
                    .first()
                )
                if last_activity:
                    last_at = _ensure_tz(last_activity.created_at)
                elif lead.last_contacted_at:
                    last_at = _ensure_tz(lead.last_contacted_at)
                else:
                    last_at = _ensure_tz(lead.created_at)
                inactive_days = (now - last_at).days
                AutomationEngine.trigger(
                    "lead_no_activity",
                    {"lead_id": lead.id, "inactive_days": inactive_days},
                    org_id,
                )
                triggered += 1
        return triggered

    @staticmethod
    def run_task_overdue_checks() -> int:
        from app.tasks.models import Task

        now = _utc_now()
        triggered = 0
        tasks = Task.query.filter(
            Task.status.in_(("pending", "in_progress")),
            Task.due_date < now,
            Task.lead_id.isnot(None),
        ).all()
        cutoff = now - timedelta(hours=24)
        for task in tasks:
            overdue_hours = (now - _ensure_tz(task.due_date)).total_seconds() / 3600
            recent_logs = AutomationLog.query.filter(
                AutomationLog.organization_id == task.organization_id,
                AutomationLog.created_at >= cutoff,
                AutomationLog.result.in_(("success", "failed")),
            ).all()
            if any((log.trigger_data or {}).get("task_id") == task.id for log in recent_logs):
                continue
            AutomationEngine.trigger(
                "task_overdue",
                {
                    "lead_id": task.lead_id,
                    "task_id": task.id,
                    "overdue_hours": overdue_hours,
                },
                task.organization_id,
            )
            triggered += 1
        return triggered


class AutomationService:
    @staticmethod
    def list_automations(organization_id: int) -> list[Automation]:
        return (
            Automation.query.filter_by(organization_id=organization_id)
            .options(joinedload(Automation.actions))
            .order_by(Automation.name.asc())
            .all()
        )

    @staticmethod
    def create(
        data: dict,
        organization_id: int,
        user_id: int | None,
    ) -> Automation:
        trigger_type = data.get("trigger_type")
        if trigger_type not in TRIGGER_TYPES:
            raise AutomationServiceError("Invalid trigger type.", "validation_error")
        name = (data.get("name") or "").strip()
        if not name:
            raise AutomationServiceError("Name is required.", "validation_error")

        automation = Automation(
            organization_id=organization_id,
            name=name,
            description=(data.get("description") or "").strip() or None,
            is_active=bool(data.get("is_active", True)),
            trigger_type=trigger_type,
            trigger_config=data.get("trigger_config") or {},
            created_by=user_id,
        )
        db.session.add(automation)
        db.session.flush()

        for idx, action_data in enumerate(data.get("actions") or []):
            AutomationService._add_action(automation, action_data, idx)
        db.session.flush()
        return automation

    @staticmethod
    def update(automation_id: int, data: dict, organization_id: int) -> Automation:
        automation = get_automation_for_org(automation_id, organization_id)
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                raise AutomationServiceError("Name is required.", "validation_error")
            automation.name = name
        if "description" in data:
            automation.description = (data.get("description") or "").strip() or None
        if "is_active" in data:
            automation.is_active = bool(data["is_active"])
        if "trigger_type" in data:
            if data["trigger_type"] not in TRIGGER_TYPES:
                raise AutomationServiceError("Invalid trigger type.", "validation_error")
            automation.trigger_type = data["trigger_type"]
        if "trigger_config" in data:
            automation.trigger_config = data["trigger_config"] or {}

        if "actions" in data:
            AutomationAction.query.filter_by(automation_id=automation.id).delete()
            for idx, action_data in enumerate(data["actions"] or []):
                AutomationService._add_action(automation, action_data, idx)
        db.session.flush()
        return automation

    @staticmethod
    def _add_action(automation: Automation, action_data: dict, order_index: int) -> AutomationAction:
        action_type = action_data.get("action_type")
        if action_type not in ACTION_TYPES:
            raise AutomationServiceError("Invalid action type.", "validation_error")
        config = dict(action_data.get("action_config") or {})
        if action_type == "send_webhook":
            config = _prepare_webhook_action_config(config)
        action = AutomationAction(
            automation_id=automation.id,
            order_index=order_index,
            action_type=action_type,
            action_config=config,
        )
        db.session.add(action)
        return action

    @staticmethod
    def toggle_active(automation_id: int, organization_id: int) -> Automation:
        automation = get_automation_for_org(automation_id, organization_id)
        automation.is_active = not automation.is_active
        db.session.flush()
        return automation

    @staticmethod
    def get_logs(automation_id: int, organization_id: int, limit: int = 50) -> list[AutomationLog]:
        get_automation_for_org(automation_id, organization_id)
        return (
            AutomationLog.query.filter_by(
                automation_id=automation_id, organization_id=organization_id
            )
            .order_by(AutomationLog.created_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def delete(automation_id: int, organization_id: int) -> None:
        automation = get_automation_for_org(automation_id, organization_id)
        db.session.delete(automation)
        db.session.flush()
