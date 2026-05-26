from app.email.models import ALLOWED_TEMPLATE_VARIABLES, EmailTemplate
from app.extensions import db

SYSTEM_TEMPLATES = [
    {
        "name": "Initial Outreach",
        "subject_template": "Quick intro — {{company}}",
        "body_html_template": (
            "<p>Hi {{first_name}},</p>"
            "<p>I wanted to reach out regarding {{company}}.</p>"
            "<p>{{ai_summary}}</p>"
            "<p>Best,<br>{{sender_name}}</p>"
        ),
        "body_text_template": (
            "Hi {{first_name}},\n\n"
            "I wanted to reach out regarding {{company}}.\n\n"
            "{{ai_summary}}\n\n"
            "Best,\n{{sender_name}}"
        ),
        "variables": sorted(ALLOWED_TEMPLATE_VARIABLES),
    },
    {
        "name": "Follow-up",
        "subject_template": "Following up — {{company}}",
        "body_html_template": (
            "<p>Hi {{first_name}},</p>"
            "<p>Just following up on my previous message.</p>"
            "<p>Best,<br>{{sender_name}}</p>"
        ),
        "body_text_template": (
            "Hi {{first_name}},\n\n"
            "Just following up on my previous message.\n\n"
            "Best,\n{{sender_name}}"
        ),
        "variables": ["first_name", "company", "sender_name"],
    },
    {
        "name": "Demo Request",
        "subject_template": "Demo for {{company}}?",
        "body_html_template": (
            "<p>Hi {{first_name}},</p>"
            "<p>Would you be open to a short demo for {{company}}?</p>"
            "<p>Best,<br>{{sender_name}}</p>"
        ),
        "body_text_template": (
            "Hi {{first_name}},\n\n"
            "Would you be open to a short demo for {{company}}?\n\n"
            "Best,\n{{sender_name}}"
        ),
        "variables": ["first_name", "company", "sender_name"],
    },
]


def seed_system_email_templates() -> list[EmailTemplate]:
    created = []
    for spec in SYSTEM_TEMPLATES:
        existing = EmailTemplate.query.filter_by(
            organization_id=None,
            name=spec["name"],
        ).first()
        if existing:
            continue
        template = EmailTemplate(
            organization_id=None,
            name=spec["name"],
            subject_template=spec["subject_template"],
            body_html_template=spec["body_html_template"],
            body_text_template=spec.get("body_text_template"),
            variables=spec.get("variables"),
            created_by=None,
        )
        db.session.add(template)
        created.append(template)
    if created:
        db.session.flush()
    return created
