from datetime import datetime, timezone

from flask_wtf import FlaskForm
from wtforms import BooleanField, DateTimeLocalField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.tasks.models import TASK_PRIORITIES, TASK_TYPES


class TaskForm(FlaskForm):
    title = StringField("Title", validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=5000)])
    type = SelectField(
        "Type",
        choices=[
            ("call", "Call"),
            ("email", "Email"),
            ("follow_up", "Follow-up"),
            ("meeting", "Meeting"),
            ("other", "Other"),
        ],
        default="follow_up",
    )
    priority = SelectField(
        "Priority",
        choices=[(p, p.capitalize()) for p in TASK_PRIORITIES],
        default="normal",
    )
    due_date = DateTimeLocalField("Due date", format="%Y-%m-%dT%H:%M", validators=[DataRequired()])
    assigned_to = SelectField("Assign to", coerce=int, validators=[DataRequired()])
    enable_reminder = BooleanField("Reminder", default=False)
    reminder_at = DateTimeLocalField(
        "Reminder at",
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
    )

    def to_service_data(self) -> dict:
        due = self.due_date.data
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        reminder = None
        if self.enable_reminder.data and self.reminder_at.data:
            reminder = self.reminder_at.data
            if reminder.tzinfo is None:
                reminder = reminder.replace(tzinfo=timezone.utc)
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
    title = StringField("Title", validators=[DataRequired(), Length(max=200)])
    type = SelectField(
        "Type",
        choices=[(t, t.replace("_", " ").title()) for t in TASK_TYPES],
        default="follow_up",
    )
    priority = SelectField(
        "Priority",
        choices=[(p, p.capitalize()) for p in TASK_PRIORITIES],
        default="normal",
    )
    due_date = DateTimeLocalField("Due date", format="%Y-%m-%dT%H:%M", validators=[DataRequired()])

    def to_service_data(self) -> dict:
        due = self.due_date.data
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return {
            "title": self.title.data,
            "type": self.type.data,
            "priority": self.priority.data,
            "due_date": due,
        }
