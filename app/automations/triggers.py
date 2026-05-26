"""Safe entry point for firing automations from other modules."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def fire_automation_trigger(
    event_type: str,
    payload: dict,
    organization_id: int,
) -> None:
    """Never raises — failures are logged only."""
    try:
        from app.automations.services import AutomationEngine

        AutomationEngine.trigger(event_type, payload, organization_id)
    except Exception:
        logger.exception(
            "Automation trigger failed for %s org=%s", event_type, organization_id
        )
