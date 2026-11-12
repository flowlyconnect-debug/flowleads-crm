from flask_wtf import FlaskForm
from wtforms import IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError


class AnonymizeLeadForm(FlaskForm):
    password = PasswordField("Salasana", validators=[DataRequired()])
    reason = TextAreaField("Syy", validators=[Optional(), Length(max=500)])
    submit = SubmitField("Anonymisoi tiedot")


LEGAL_BASIS_CHOICES = [
    ("legitimate_interest", "Oikeutettu etu (yritysten välinen myynti)"),
    ("consent", "Suostumus"),
    ("contract", "Sopimus"),
]
MIN_RETENTION_DAYS = 180


class PrivacySettingsForm(FlaskForm):
    gdpr_default_legal_basis = SelectField(
        "Käsittelyperuste uusille liideille", choices=LEGAL_BASIS_CHOICES, validators=[Optional()]
    )
    gdpr_retention_days = IntegerField(
        "Säilytysaika passiivisille liideille (päivää)",
        validators=[
            Optional(),
            NumberRange(min=MIN_RETENTION_DAYS, max=3650, message=f"Säilytysaika {MIN_RETENTION_DAYS}–3650 päivää."),
        ],
    )
    privacy_policy_url = StringField("Tietosuojaselosteen osoite (https://…)", validators=[Optional(), Length(max=500)])

    def validate_privacy_policy_url(self, field):
        value = (field.data or "").strip()
        if value and not value.lower().startswith("https://"):
            raise ValidationError("Tietosuojaselosteen osoitteen pitää alkaa https://.")
    data_controller_name = StringField("Rekisterinpitäjä", validators=[Optional(), Length(max=255)])
    data_controller_email = StringField("Rekisterinpitäjän sähköposti", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Tallenna")
