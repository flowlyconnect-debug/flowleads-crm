from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, SelectMultipleField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class WebFormCreateForm(FlaskForm):
    name = StringField("Nimi", validators=[DataRequired(), Length(max=200)])
    title = StringField("Otsikko", validators=[DataRequired(), Length(max=300)])
    description = TextAreaField("Kuvaus", validators=[Optional(), Length(max=5000)])


class WebFormSettingsForm(FlaskForm):
    submit_button_text = StringField("Lähetyspainike", validators=[DataRequired(), Length(max=100)])
    success_message = StringField(
        "Kiitosviesti",
        validators=[DataRequired(), Length(max=500)],
    )
    default_stage_id = SelectField("Oletusvaihe", coerce=int, validators=[Optional()])
    default_assigned_to = SelectField("Oletusvastuuhenkilö", coerce=int, validators=[Optional()])
    auto_enroll_sequence_id = SelectField("Sekvenssi", coerce=int, validators=[Optional()])
    notify_users = SelectMultipleField("Ilmoita käyttäjille", coerce=int, validators=[Optional()])
    is_active = BooleanField("Aktiivinen")
