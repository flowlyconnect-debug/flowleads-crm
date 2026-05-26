from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, SelectField, StringField, TextAreaField
from wtforms.fields import DateTimeLocalField
from wtforms.validators import DataRequired, Length, Optional


DURATION_CHOICES = [
    (15, "15 min"),
    (30, "30 min"),
    (45, "45 min"),
    (60, "60 min"),
    (90, "90 min"),
]


class ScheduleMeetingForm(FlaskForm):
    title = StringField("Otsikko", validators=[DataRequired(), Length(max=500)])
    start_at = DateTimeLocalField("Alkaa", validators=[DataRequired()], format="%Y-%m-%dT%H:%M")
    duration_minutes = SelectField(
        "Kesto",
        choices=DURATION_CHOICES,
        coerce=int,
        default=30,
    )
    description = TextAreaField("Kuvaus", validators=[Optional(), Length(max=5000)])
    attendees = StringField(
        "Osallistujat (pilkuilla erotettu)",
        validators=[Optional(), Length(max=2000)],
    )
    video_meeting = BooleanField("Videotapaaminen (Meet / Teams)", default=True)
    location = StringField("Sijainti", validators=[Optional(), Length(max=500)])
    lead_id = HiddenField()
