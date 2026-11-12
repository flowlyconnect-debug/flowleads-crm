from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, DecimalField, IntegerField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Optional


class CommaDecimalField(DecimalField):
    """Accepts the Finnish decimal comma: "25,5" -> 25.5."""

    def process_formdata(self, valuelist):
        if valuelist:
            valuelist = [str(v).replace(",", ".").replace(" ", "") for v in valuelist]
        super().process_formdata(valuelist)


class ProposalSettingsForm(FlaskForm):
    proposal_move_lead_to_won_on_accept = BooleanField(
        "Siirrä liidi Voitettu-vaiheeseen, kun asiakas hyväksyy tarjouksen"
    )
    proposal_default_valid_days = IntegerField(
        "Tarjous voimassa (päivää)",
        validators=[InputRequired(message="Anna voimassaoloaika päivinä."), NumberRange(min=1, max=365, message="Voimassaolo 1–365 päivää.")],
    )
    proposal_default_tax_percent = CommaDecimalField(
        "Oletus-ALV % (0 = käännetty verovelvollisuus)",
        places=2,
        validators=[InputRequired(message="Anna ALV-prosentti (esim. 25,5 tai 0)."), NumberRange(min=0, max=100, message="ALV 0–100 %.")],
    )
    proposal_default_notes = TextAreaField("Oletushuomautukset", validators=[Optional()])
    logo_url = StringField("Logon osoite (https://…)", validators=[Optional(), Length(max=500)])
    header_html = TextAreaField("PDF/HTML ylätunniste", validators=[Optional()])
    footer_html = TextAreaField("PDF/HTML alatunniste", validators=[Optional()])
    submit = SubmitField("Tallenna")


class PublicAcceptForm(FlaskForm):
    signature_name = StringField("Allekirjoituksen nimi", validators=[DataRequired()])
    submit = SubmitField("Hyväksy tarjous")


class PublicDeclineForm(FlaskForm):
    reason = TextAreaField("Syy (valinnainen)", validators=[Optional()])
    submit = SubmitField("Hylkää tarjous")
