from flask_wtf import FlaskForm
from wtforms import HiddenField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional


class ComposeEmailForm(FlaskForm):
    template_id = SelectField("Malli", coerce=int, validators=[Optional()], choices=[])
    subject = StringField("Aihe", validators=[DataRequired(), Length(max=255)])
    body_html = HiddenField("Viesti", validators=[DataRequired()])
    submit = SubmitField("Lähetä sähköposti")


class EmailTemplateForm(FlaskForm):
    name = StringField("Mallin nimi", validators=[DataRequired(), Length(max=120)])
    subject_template = StringField("Aihe", validators=[DataRequired(), Length(max=255)])
    body_html_template = TextAreaField("Viesti (HTML)", validators=[DataRequired()])
    body_text_template = TextAreaField("Viesti pelkkänä tekstinä (valinnainen)", validators=[Optional()])
    submit = SubmitField("Tallenna malli")


class OrganizationEmailSettingsForm(FlaskForm):
    email_from_name = StringField("Lähettäjän nimi", validators=[Optional(), Length(max=120)])
    email_from_email = StringField("Lähettäjän sähköposti", validators=[Optional(), Email(), Length(max=255)])
    mailgun_domain = StringField("Lähetysdomain (valinnainen)", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Tallenna asetukset")
