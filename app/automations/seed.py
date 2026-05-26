"""Seed default automations for a new organization."""

from __future__ import annotations

from app.automations.constants import ACTION_TYPES, TRIGGER_TYPES
from app.automations.models import Automation, AutomationAction
from app.extensions import db
from app.leads.models import PipelineStage
from app.sequences.models import EmailSequence
from app.users.models import User


DEFAULT_AUTOMATION_NAMES = (
    "Passiivinen liidi",
    "Korkean potentiaalin liidi",
    "Tarjous lähetetty",
)


def seed_default_automations(organization_id: int, created_by: int | None = None) -> list[Automation]:
    existing = {
        a.name
        for a in Automation.query.filter_by(organization_id=organization_id).all()
    }
    created = []

    admin = (
        User.query.filter_by(organization_id=organization_id, role="admin", is_active=True)
        .order_by(User.id.asc())
        .first()
    )
    creator_id = created_by or (admin.id if admin else None)

    if "Passiivinen liidi" not in existing:
        automation = Automation(
            organization_id=organization_id,
            name="Passiivinen liidi",
            description="Luo seurantatehtävä kun liidillä ei ole aktiviteettia 14 päivään.",
            is_active=True,
            trigger_type="lead_no_activity",
            trigger_config={"days": 14},
            created_by=creator_id,
        )
        db.session.add(automation)
        db.session.flush()
        db.session.add(
            AutomationAction(
                automation_id=automation.id,
                order_index=0,
                action_type="create_task",
                action_config={
                    "title": "Ota yhteyttä",
                    "type": "follow_up",
                    "priority": "high",
                    "due_days": 1,
                    "assign_to": "owner",
                },
            )
        )
        created.append(automation)

    if "Korkean potentiaalin liidi" not in existing:
        automation = Automation(
            organization_id=organization_id,
            name="Korkean potentiaalin liidi",
            description="Ilmoita ja osoita liidi kun pistemäärä ylittää 80.",
            is_active=True,
            trigger_type="lead_score_changed",
            trigger_config={"threshold": 80, "operator": "crosses_above"},
            created_by=creator_id,
        )
        db.session.add(automation)
        db.session.flush()
        if admin:
            db.session.add(
                AutomationAction(
                    automation_id=automation.id,
                    order_index=0,
                    action_type="notify_user",
                    action_config={
                        "user_id": admin.id,
                        "title": "Korkean potentiaalin liidi",
                        "message": "{{lead.display_name}} saavutti pistemäärän {{lead.score}}.",
                        "type": "high_potential_lead",
                    },
                )
            )
        db.session.add(
            AutomationAction(
                automation_id=automation.id,
                order_index=1,
                action_type="assign_lead",
                action_config={"assign_to": "owner"},
            )
        )
        created.append(automation)

    proposal_stage = PipelineStage.query.filter_by(
        organization_id=organization_id, name="Proposal Sent"
    ).first()
    follow_up = EmailSequence.query.filter_by(
        organization_id=organization_id, name="Follow-up"
    ).first()

    if "Tarjous lähetetty" not in existing and proposal_stage:
        automation = Automation(
            organization_id=organization_id,
            name="Tarjous lähetetty",
            description="Lisää liidi Follow-up-sekvenssiin kun vaihe on Proposal Sent.",
            is_active=True,
            trigger_type="lead_stage_changed",
            trigger_config={"to_stage_id": proposal_stage.id},
            created_by=creator_id,
        )
        db.session.add(automation)
        db.session.flush()
        enroll_config: dict = {}
        if follow_up:
            enroll_config["sequence_id"] = follow_up.id
        else:
            enroll_config["sequence_name"] = "Follow-up"
        db.session.add(
            AutomationAction(
                automation_id=automation.id,
                order_index=0,
                action_type="enroll_in_sequence",
                action_config=enroll_config,
            )
        )
        created.append(automation)

    if created:
        db.session.flush()
    return created
