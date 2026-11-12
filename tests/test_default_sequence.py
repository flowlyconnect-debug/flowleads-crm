"""Every customer starts with a ready-made "Taloyhtiöt" email sequence, so the
Kohteet page can start a sequence on transfer from day one."""

from unittest.mock import patch

from app.admin.invite_services import accept_invite, create_invite
from app.admin.onboarding_services import create_customer
from app.extensions import db
from app.leads.models import Lead
from app.listings import target_services as targets
from app.sequences.defaults import DEFAULT_SEQUENCE_NAME, ensure_default_sequences
from app.sequences.models import EmailSequence
from app.sequences.services import render_sequence_template
from app.users.services import create_organization, create_user


def test_onboarded_customer_gets_active_default_sequence(app):
    with app.app_context():
        actor = create_user("actor-seq@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        result = create_customer(
            actor=actor,
            organization_name="Sekvenssi Oy",
            admin_email="admin@sekvenssi.fi",
            admin_password="securepassword1",
            remonttityyppi="Putkiremontti",
        )
        db.session.commit()
        sequence = EmailSequence.query.filter_by(organization_id=result.organization.id).one()
        assert sequence.name == DEFAULT_SEQUENCE_NAME
        assert sequence.is_active is True
        assert [s.delay_days for s in sorted(sequence.steps, key=lambda s: s.order_index)] == [0, 4, 6]
        assert sequence.steps[0].body_html_template.startswith("<p>")
        # offered on the Kohteet page right away
        assert [s.id for s in targets.sequences_for_transfer(result.organization.id)] == [sequence.id]


@patch("app.email.services._mailgun_send")
def test_invited_customer_gets_default_sequence(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    with app.app_context():
        actor = create_user("actor-seq2@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        result = create_invite(
            actor=actor,
            organization_name="Kutsu Sekvenssi Oy",
            admin_email="admin@kutsusekvenssi.fi",
            remonttityyppi="Putkiremontti",
            allowed_features=["sequences"],
        )
        db.session.commit()
        accept_invite(result.invite, password="brandnewpassword1", regions=["Uusimaa"], enabled_features=["sequences"])
        db.session.commit()
        assert EmailSequence.query.filter_by(organization_id=result.organization.id).count() == 1


def test_default_sequence_is_not_added_twice_or_over_own_sequences(app):
    with app.app_context():
        org = create_organization("Oma Oy", "oma-oy")
        db.session.flush()
        db.session.add(EmailSequence(organization_id=org.id, name="Oma", trigger_type="manual"))
        db.session.commit()
        assert ensure_default_sequences(org.id) is None
        empty = create_organization("Tyhjä Oy", "tyhja-oy")
        db.session.flush()
        assert ensure_default_sequences(empty.id) is not None
        assert ensure_default_sequences(empty.id) is None
        db.session.commit()
        assert EmailSequence.query.filter_by(organization_id=empty.id).count() == 1


def test_sequences_are_not_offered_without_the_sequences_module(app):
    with app.app_context():
        org = create_organization("Ilman Moduulia Oy", "ilman-moduulia")
        db.session.flush()
        ensure_default_sequences(org.id)
        org.enabled_features = ["automations"]
        db.session.commit()
        assert targets.sequences_for_transfer(org.id) == []


def test_catalogue_variables_fill_the_default_texts(app):
    with app.app_context():
        org = create_organization("Putkimestarit Oy", "putkimestarit")
        db.session.flush()
        from app.leads.models import PipelineStage

        stage = PipelineStage.query.filter_by(organization_id=org.id).order_by(PipelineStage.order_index).first()
        lead = Lead(
            organization_id=org.id,
            stage_id=stage.id,
            company="As Oy Merikortteli",
            email="isannoitsija@example.com",
            ai_contact_info={
                "taloyhtio": "As Oy Merikortteli",
                "remonttityyppi": "Putkiremontti",
                "remontin_ajankohta": "2027",
                "kaupunki": "Helsinki",
                "isannoitsija_henkilo": "Laura Mäkinen",
            },
        )
        db.session.add(lead)
        db.session.commit()
        text = render_sequence_template(
            "{{company}} ({{remonttityyppi}}) {{ajankohta}} {{kaupunki}} {{isannoitsija}} / {{yritys}}", lead
        )
        assert text == "As Oy Merikortteli (putkiremontti) 2027 Helsinki Laura Mäkinen / Putkimestarit Oy"
        # a lead from elsewhere simply gets empty catalogue values
        plain = Lead(organization_id=org.id, stage_id=stage.id, company="Firma Oy", email="a@example.com")
        db.session.add(plain)
        db.session.commit()
        assert render_sequence_template("{{remonttityyppi}}|{{yritys}}", plain) == "|Putkimestarit Oy"
