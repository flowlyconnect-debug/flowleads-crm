from datetime import datetime, timezone

from flask_wtf import FlaskForm
from wtforms import BooleanField, DateTimeLocalField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.tasks.models import TASK_PRIORITIES, TASK_TYPES

TASK_TYPE_LABELS_FI = {
    "call": "Soitto",
    "email": "Sähköposti",
    "follow_up": "Seuranta",
    "meeting": "Tapaaminen",
    "other": "Muu",
}
TASK_PRIORITY_LABELS_FI = {"low": "Matala", "normal": "Normaali", "high": "Korkea", "urgent": "Kiireellinen"}


class TaskForm(FlaskForm):
    title = StringField("Otsikko", validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Kuvaus", validators=[Optional(), Length(max=5000)])
    type = SelectField(
        "Tyyppi",
        choices=[(t, TASK_TYPE_LABELS_FI.get(t, t)) for t in ("call", "email", "follow_up", "meeting", "other")],
        default="follow_up",
    )
    priority = SelectField(
        "Prioriteetti",
        choices=[(p, TASK_PRIORITY_LABELS_FI.get(p, p)) for p in TASK_PRIORITIES],
        default="normal",
    )
    due_date = DateTimeLocalField("Eräpäivä", format="%Y-%m-%dT%H:%M", validators=[DataRequired()])
    assigned_to = SelectField("Vastuuhenkilö", coerce=int, validators=[DataRequired()])
    enable_reminder = BooleanField("Muistutus", default=False)
    reminder_at = DateTimeLocalField(
        "Muistutuksen aika",
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
    )

    def to_service_data(self) -> dict:
        from app.tasks.services import local_to_utc

        # the browser's datetime-local value is Finnish time
        due = local_to_utc(self.due_date.data)
        reminder = None
        if self.enable_reminder.data and self.reminder_at.data:
            reminder = local_to_utc(self.reminder_at.data)
        return {
            "title": self.title.data,
            "description": self.description.data,
            "type": self.type.data,
            "priority": self.priority.data,
            "due_date": due,
            "assigned_to": self.assigned_to.data,
            "reminder_at": reminder,
        }


class QuickTaskForm(FlaskForm):
    title = StringField("Otsikko", validators=[DataRequired(), Length(max=200)])
    type = SelectField(
        "Tyyppi",
        choices=[(t, TASK_TYPE_LABELS_FI.get(t, t)) for t in TASK_TYPES],
        default="follow_up",
    )
    priority = SelectField(
        "Prioriteetti",
        choices=[(p, TASK_PRIORITY_LABELS_FI.get(p, p)) for p in TASK_PRIORITIES],
        default="normal",
    )
    due_date = DateTimeLocalField("Eräpäivä", format="%Y-%m-%dT%H:%M", validators=[DataRequired()])

    def to_service_data(self) -> dict:
        from app.tasks.services import local_to_utc

        due = local_to_utc(self.due_date.data)
        return {
            "title": self.title.data,
            "type": self.type.data,
            "priority": self.priority.data,
            "due_date": due,
        }
