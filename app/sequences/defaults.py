"""Ready-made email sequence every customer organization starts with.

Onboarding (`app/admin/onboarding_services.py`, `invite_services.py`) and the
data migration `f0a1b2c3d4e5` call `ensure_default_sequences()`, so the Kohteet
page can offer "Siirrä liideiksi ja lisää sekvenssiin" from day one. The texts
use the catalogue variables of `render_sequence_template()`: {{company}} is the
taloyhtiö, {{remonttityyppi}} the renovation and {{yritys}} the sender's own
organization. The customer edits them in Sekvenssit like any other sequence.
"""

from __future__ import annotations

DEFAULT_SEQUENCE_NAME = "Taloyhtiöt: isännöitsijän lähestyminen"
DEFAULT_SEQUENCE_DESCRIPTION = (
    "Valmis kolmen viestin sekvenssi Kohteet-sivulta siirretyille taloyhtiöille. "
    "Muokkaa viestit omiksesi ennen käyttöä."
)

# delay_days counts from the previous step (the first one goes out at once).
DEFAULT_SEQUENCE_STEPS: tuple[dict, ...] = (
    {
        "delay_days": 0,
        "subject": "{{company}}: tuleva remontti",
        "body": (
            "Hei,\n\n"
            "huomasimme, että taloyhtiössä {{company}} on suunnitteilla remontti ({{remonttityyppi}}). "
            "Olemme {{yritys}} ja teemme vastaavia urakoita taloyhtiöille.\n\n"
            "Kerromme mielellämme, miten olemme toteuttaneet samanlaisia kohteita ja mitä hallituksen "
            "kannattaa ottaa huomioon suunnittelussa ja kilpailutuksessa. Sopisiko, että soitan ensi "
            "viikolla?\n\n"
            "Ystävällisin terveisin\n{{yritys}}"
        ),
        "skip_if_replied": False,
    },
    {
        "delay_days": 4,
        "subject": "{{company}}: referenssit hallitukselle",
        "body": (
            "Hei,\n\n"
            "palaan vielä viestiini taloyhtiön {{company}} remontista. Jos hanke on ajankohtainen, "
            "voimme toimittaa referenssit vastaavista kohteista hallituksen kokousta varten.\n\n"
            "Terveisin\n{{yritys}}"
        ),
        "skip_if_replied": True,
    },
    {
        "delay_days": 6,
        "subject": "{{company}}: palataanko asiaan myöhemmin?",
        "body": (
            "Hei,\n\n"
            "en halua täyttää postilaatikkoasi, joten tämä on viimeinen viestini aiheesta. Jos remontti "
            "tulee ajankohtaiseksi myöhemmin, vastaa tähän viestiin, niin palaamme asiaan.\n\n"
            "Terveisin\n{{yritys}}"
        ),
        "skip_if_replied": True,
    },
)


def _html(text: str) -> str:
    from markupsafe import escape

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{escape(p)}</p>".replace("\n", "<br>") for p in paragraphs)


def step_rows() -> list[dict]:
    """The steps as column values for `email_sequence_steps`."""
    return [
        {
            "order_index": index,
            "delay_days": step["delay_days"],
            "delay_hours": 0,
            "subject_template": step["subject"],
            "body_text_template": step["body"],
            "body_html_template": _html(step["body"]),
            "condition": {"skip_if_replied": True} if step["skip_if_replied"] else None,
        }
        for index, step in enumerate(DEFAULT_SEQUENCE_STEPS)
    ]


def ensure_default_sequences(organization_id: int, *, created_by: int | None = None):
    """Give an organization that has no email sequence yet the ready-made one.
    An organization with sequences of its own is left alone. Returns the new
    sequence or None; the caller commits."""
    from app.extensions import db
    from app.sequences.models import EmailSequence, EmailSequenceStep

    if EmailSequence.query.filter_by(organization_id=organization_id).first() is not None:
        return None
    sequence = EmailSequence(
        organization_id=organization_id,
        name=DEFAULT_SEQUENCE_NAME,
        description=DEFAULT_SEQUENCE_DESCRIPTION,
        is_active=True,
        trigger_type="manual",
        created_by=created_by,
    )
    for row in step_rows():
        sequence.steps.append(EmailSequenceStep(**row))
    db.session.add(sequence)
    db.session.flush()
    return sequence
