from __future__ import annotations

WEBHOOK_EVENTS = {
    "lead.created": {
        "label": "Uusi liidi",
        "category": "Leads",
    },
    "lead.stage_changed": {
        "label": "Liidin vaihe muuttui",
        "category": "Leads",
    },
    "lead.score_updated": {
        "label": "Pisteytys päivittyi",
        "category": "Leads",
    },
    "lead.assigned": {
        "label": "Liidi osoitettiin",
        "category": "Leads",
    },
    "task.overdue": {
        "label": "Tehtävä myöhässä",
        "category": "Tasks",
    },
    "proposal.viewed": {
        "label": "Tarjous katsottu",
        "category": "Proposals",
    },
    "proposal.accepted": {
        "label": "Tarjous hyväksytty",
        "category": "Proposals",
    },
    "proposal.declined": {
        "label": "Tarjous hylätty",
        "category": "Proposals",
    },
    "sequence.completed": {
        "label": "Sekvenssi valmis",
        "category": "Sequences",
    },
    "lead.high_score": {
        "label": "Korkea pisteytys (≥80)",
        "category": "Leads",
    },
}

WEBHOOK_EVENT_GROUP_LABELS = {
    "Leads": "Liidit",
    "Tasks": "Tehtävät",
    "Proposals": "Tarjoukset",
    "Sequences": "Sekvenssit",
}

WEBHOOK_EVENT_GROUPS = {
    "Leads": [k for k, v in WEBHOOK_EVENTS.items() if v["category"] == "Leads"],
    "Tasks": [k for k, v in WEBHOOK_EVENTS.items() if v["category"] == "Tasks"],
    "Proposals": [k for k, v in WEBHOOK_EVENTS.items() if v["category"] == "Proposals"],
    "Sequences": [k for k, v in WEBHOOK_EVENTS.items() if v["category"] == "Sequences"],
}

