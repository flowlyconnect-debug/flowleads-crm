from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class AnonymizeLeadForm(FlaskForm):
    password = PasswordField("Salasana", validators=[DataRequired()])
    reason = TextAreaField("Syy", validators=[Optional(), Length(max=500)])
    submit = SubmitField("Anonymisoi tiedot")


class PrivacySettingsForm(FlaskForm):
    gdpr_default_legal_basis = StringField("Oletusperuste", validators=[Optional(), Length(max=64)])
    gdpr_retention_days = StringField("Säilytysaika (päivää)", validators=[Optional(), Length(max=5)])
    privacy_policy_url = StringField("Tietosuojaseloste URL", validators=[Optional(), Length(max=500)])
    data_controller_name = StringField("Rekisterinpitäjä", validators=[Optional(), Length(max=255)])
    data_controller_email = StringField("Rekisterinpitäjän sähköposti", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Tallenna")
