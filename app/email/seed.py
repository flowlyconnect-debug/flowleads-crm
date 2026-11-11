from app.email.models import ALLOWED_TEMPLATE_VARIABLES, EmailTemplate
from app.extensions import db

SYSTEM_TEMPLATES = [
    {
        "name": "Ensimmäinen yhteydenotto",
        "subject_template": "Yhteydenotto — {{company}}",
        "body_html_template": (
            "<p>Hei {{first_name}},</p>"
            "<p>Otan yhteyttä koskien kohdetta {{company}}.</p>"
            "<p>{{ai_summary}}</p>"
            "<p>Ystävällisin terveisin,<br>{{sender_name}}</p>"
        ),
        "body_text_template": (
            "Hei {{first_name}},\n\n"
            "Otan yhteyttä koskien kohdetta {{company}}.\n\n"
            "{{ai_summary}}\n\n"
            "Ystävällisin terveisin,\n{{sender_name}}"
        ),
        "variables": sorted(ALLOWED_TEMPLATE_VARIABLES),
    },
    {
        "name": "Seurantaviesti",
        "subject_template": "Palaan asiaan — {{company}}",
        "body_html_template": (
            "<p>Hei {{first_name}},</p>"
            "<p>Palaan aiempaan viestiini. Ehditkö katsoa asiaa?</p>"
            "<p>Ystävällisin terveisin,<br>{{sender_name}}</p>"
        ),
        "body_text_template": (
            "Hei {{first_name}},\n\n"
            "Palaan aiempaan viestiini. Ehditkö katsoa asiaa?\n\n"
            "Ystävällisin terveisin,\n{{sender_name}}"
        ),
        "variables": ["first_name", "company", "sender_name"],
    },
    {
        "name": "Tapaamisehdotus",
        "subject_template": "Sovitaanko lyhyt tapaaminen? — {{company}}",
        "body_html_template": (
            "<p>Hei {{first_name}},</p>"
            "<p>Sopisiko lyhyt tapaaminen tai puhelu kohteen {{company}} tiimoilta?</p>"
            "<p>Ystävällisin terveisin,<br>{{sender_name}}</p>"
        ),
        "body_text_template": (
            "Hei {{first_name}},\n\n"
            "Sopisiko lyhyt tapaaminen tai puhelu kohteen {{company}} tiimoilta?\n\n"
            "Ystävällisin terveisin,\n{{sender_name}}"
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
        "subject_template": "Ei uusia liidejä {{ days }} päivään — {{ org_name }}",
        "body_html_template": (
            "<div style='font-family:Inter,Arial,sans-serif;color:#111827;'>"
            "<h2 style='color:#B45309;'>Ei uusia liidejä</h2>"
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


# The first three templates used to be seeded in English under these names.
# An untouched English row is replaced by the Finnish one; an edited row is kept.
_LEGACY_ENGLISH_NAMES = {
    "Initial Outreach": ("Ensimmäinen yhteydenotto", "Quick intro — {{company}}"),
    "Follow-up": ("Seurantaviesti", "Following up — {{company}}"),
    "Demo Request": ("Tapaamisehdotus", "Demo for {{company}}?"),
}


# System templates that the app itself sends (reminders, notifications). They
# are not sales emails, so they stay out of the compose and sequence pickers.
INTERNAL_TEMPLATE_NAMES = frozenset(
    {
        "task_reminder",
        "proposal_sent",
        "proposal_accepted_notification",
        "proposal_declined_notification",
        "lead_stale_alert",
    }
)


def seed_system_email_templates(commit: bool = False) -> list[EmailTemplate]:
    """Create the missing system templates. Flushes only, unless `commit` is
    set: a caller that renders ids into links (the template list page) must
    commit, otherwise the rows vanish with the request and the links 404."""
    specs = {spec["name"]: spec for spec in SYSTEM_TEMPLATES}
    changed = False
    for old_name, (new_name, old_subject) in _LEGACY_ENGLISH_NAMES.items():
        legacy = EmailTemplate.query.filter_by(organization_id=None, name=old_name).first()
        if not legacy or legacy.subject_template != old_subject:
            continue
        if EmailTemplate.query.filter_by(organization_id=None, name=new_name).first():
            continue
        spec = specs[new_name]
        legacy.name = new_name
        legacy.subject_template = spec["subject_template"]
        legacy.body_html_template = spec["body_html_template"]
        legacy.body_text_template = spec.get("body_text_template")
        legacy.variables = spec.get("variables")
        changed = True

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
    if created or changed:
        db.session.flush()
        if commit:
            db.session.commit()
    return created


def get_system_template(name: str) -> EmailTemplate | None:
    """A system template by name, seeding the defaults first when it is
    missing (a fresh database has none until someone opens the template list)."""
    template = EmailTemplate.query.filter_by(organization_id=None, name=name).first()
    if template is None:
        seed_system_email_templates()
        template = EmailTemplate.query.filter_by(organization_id=None, name=name).first()
    return template
