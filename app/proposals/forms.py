from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, DecimalField, IntegerField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, NumberRange, Optional


class ProposalSettingsForm(FlaskForm):
    proposal_move_lead_to_won_on_accept = BooleanField("Siirrä liidi Won-vaiheeseen hyväksynnän jälkeen")
    proposal_default_valid_days = IntegerField(
        "Oletus voimassaolopäivät",
        validators=[DataRequired(), NumberRange(min=1, max=365)],
    )
    proposal_default_tax_percent = DecimalField(
        "Oletus ALV %",
        places=2,
        validators=[DataRequired(), NumberRange(min=0, max=100)],
    )
    proposal_default_notes = TextAreaField("Oletushuomautukset", validators=[Optional()])
    default_valid_days = IntegerField("Mallin voimassaolopäivät", validators=[Optional(), NumberRange(min=1, max=365)])
    default_tax_percent = DecimalField("Mallin ALV %", places=2, validators=[Optional()])
    default_notes = TextAreaField("Mallin huomautukset", validators=[Optional()])
    header_html = TextAreaField("PDF/HTML ylätunniste", validators=[Optional()])
    footer_html = TextAreaField("PDF/HTML alatunniste", validators=[Optional()])
    submit = SubmitField("Tallenna")


class PublicAcceptForm(FlaskForm):
    signature_name = StringField("Allekirjoituksen nimi", validators=[DataRequired()])
    submit = SubmitField("Hyväksy tarjous")


class PublicDeclineForm(FlaskForm):
    reason = TextAreaField("Syy (valinnainen)", validators=[Optional()])
    submit = SubmitField("Hylkää tarjous")
