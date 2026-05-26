from flask_wtf import FlaskForm
from wtforms import HiddenField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional


class ComposeEmailForm(FlaskForm):
    template_id = SelectField("Template", coerce=int, validators=[Optional()], choices=[])
    subject = StringField("Subject", validators=[DataRequired(), Length(max=255)])
    body_html = HiddenField("Body HTML", validators=[DataRequired()])
    submit = SubmitField("Send email")


class EmailTemplateForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    subject_template = StringField("Subject", validators=[DataRequired(), Length(max=255)])
    body_html_template = TextAreaField("HTML body", validators=[DataRequired()])
    body_text_template = TextAreaField("Plain text (optional)", validators=[Optional()])
    submit = SubmitField("Save template")


class OrganizationEmailSettingsForm(FlaskForm):
    email_from_name = StringField("From name", validators=[Optional(), Length(max=120)])
    email_from_email = StringField("From email", validators=[Optional(), Email(), Length(max=255)])
    mailgun_domain = StringField("Mailgun domain (optional)", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Save settings")
