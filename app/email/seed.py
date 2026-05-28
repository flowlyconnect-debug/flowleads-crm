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
    {
        "name": "task_reminder",
        "subject_template": "Muistutus: {{task_title}} — {{lead_company}}",
        "body_html_template": (
            "<p>Hei {{assignee_name}},</p>"
            "<p>Muistutus tehtävästä: <strong>{{task_title}}</strong></p>"
            "<p>Eräpäivä: {{due_date}}</p>"
            "<p>Liidi: {{lead_name}} ({{lead_company}})</p>"
            "<p>— FlowLeads</p>"
        ),
        "body_text_template": (
            "Hei {{assignee_name}},\n\n"
            "Muistutus tehtävästä: {{task_title}}\n"
            "Eräpäivä: {{due_date}}\n"
            "Liidi: {{lead_name}} ({{lead_company}})\n\n"
            "— FlowLeads"
        ),
        "variables": sorted(
            {
                "task_title",
                "due_date",
                "lead_name",
                "lead_company",
                "assignee_name",
                "first_name",
            }
        ),
    },
    {
        "name": "proposal_sent",
        "subject_template": "Tarjous {{reference_number}} — {{proposal_title}}",
        "body_html_template": (
            "<p>Hei {{first_name}},</p>"
            "<p>Lähetämme tarjouksen <strong>{{reference_number}}</strong>.</p>"
            "<p><a href=\"{{proposal_url}}\">Avaa tarjous</a></p>"
            "<p>Voimassa: {{valid_until}}</p>"
            "<p>Yhteensä: {{total}}</p>"
        ),
        "body_text_template": (
            "Hei {{first_name}},\n\n"
            "Tarjous {{reference_number}}: {{proposal_url}}\n"
            "Voimassa: {{valid_until}}\nYhteensä: {{total}}\n"
        ),
        "variables": sorted(
            {
                "first_name",
                "company",
                "reference_number",
                "proposal_title",
                "proposal_url",
                "valid_until",
                "total",
            }
        ),
    },
    {
        "name": "proposal_accepted_notification",
        "subject_template": "Tarjous hyväksytty: {{reference_number}}",
        "body_html_template": (
            "<p>Tarjous <strong>{{reference_number}}</strong> hyväksyttiin.</p>"
            "<p>Asiakas: {{lead_name}}</p>"
            "<p>Summa: {{total}} {{currency}}</p>"
        ),
        "body_text_template": (
            "Tarjous {{reference_number}} hyväksytty.\n"
            "Asiakas: {{lead_name}}\nSumma: {{total}} {{currency}}\n"
        ),
        "variables": sorted({"reference_number", "lead_name", "total", "currency"}),
    },
    {
        "name": "proposal_declined_notification",
        "subject_template": "Tarjous hylätty: {{reference_number}}",
        "body_html_template": (
            "<p>Tarjous <strong>{{reference_number}}</strong> hylättiin.</p>"
            "<p>Asiakas: {{lead_name}}</p>"
        ),
        "body_text_template": "Tarjous {{reference_number}} hylättiin. Asiakas: {{lead_name}}\n",
        "variables": sorted({"reference_number", "lead_name"}),
    },
    {
        "name": "lead_stale_alert",
        "subject_template": "Ei uusia liideja {{ days }} paivaan — {{ org_name }}",
        "body_html_template": (
            "<div style='font-family:Inter,Arial,sans-serif;color:#111827;'>"
            "<h2 style='color:#B45309;'>Ei uusia liideja</h2>"
            "<p>Viimeisin liidi saapui {{ last_lead_at }} — onko kaikki kunnossa?</p>"
            "<p><a href='/settings/leads'>Tarkista liidiasetukset</a></p>"
            "<p style='margin-top:20px;color:#6B7280;font-size:12px;'>FlowLeads</p>"
            "</div>"
        ),
        "body_text_template": (
            "Viimeisin liidi saapui {{ last_lead_at }} — onko kaikki kunnossa?\n"
            "Tarkista liidiasetukset: /settings/leads"
        ),
        "variables": sorted({"org_name", "last_lead_at", "days"}),
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
