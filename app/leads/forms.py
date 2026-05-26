from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    HiddenField,
    IntegerField,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import Length, NumberRange, Optional


class LeadForm(FlaskForm):
    first_name = StringField("First name", validators=[Optional(), Length(max=100)])
    last_name = StringField("Last name", validators=[Optional(), Length(max=100)])
    email = StringField("Email", validators=[Optional(), Length(max=255)])
    phone = StringField("Phone", validators=[Optional(), Length(max=50)])
    company = StringField("Company", validators=[Optional(), Length(max=255)])
    title = StringField("Title", validators=[Optional(), Length(max=150)])
    website = StringField("Website", validators=[Optional(), Length(max=500)])
    linkedin_url = StringField("LinkedIn", validators=[Optional(), Length(max=500)])
    stage_id = SelectField("Stage", coerce=int, validators=[Optional()])
    assigned_to = SelectField("Assigned to", coerce=int, validators=[Optional()])
    source = SelectField(
        "Source",
        choices=[("manual", "Manual"), ("n8n", "n8N"), ("import", "Import")],
        default="manual",
    )
    source_ref = StringField("Source reference", validators=[Optional(), Length(max=255)])
    score = IntegerField("Score", validators=[Optional(), NumberRange(min=0, max=100)])
    score_reason = TextAreaField("Score reason", validators=[Optional()])
    notes = TextAreaField("Notes", validators=[Optional()])
    tags = StringField("Tags (comma-separated)", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("Save")


class LeadFilterForm(FlaskForm):
    search = StringField("Search", validators=[Optional(), Length(max=255)])
    stage_id = SelectField("Stage", coerce=int, validators=[Optional()])
    source = SelectField(
        "Source",
        choices=[("", "All"), ("manual", "Manual"), ("n8n", "n8N"), ("import", "Import")],
        validators=[Optional()],
    )
    assigned_to = SelectField("Assigned to", coerce=int, validators=[Optional()])
    status = SelectField(
        "Status",
        choices=[("", "Active only"), ("archived", "Archived"), ("won", "Won"), ("lost", "Lost")],
        validators=[Optional()],
    )
    score_min = IntegerField("Min score", validators=[Optional(), NumberRange(min=0, max=100)])
    score_max = IntegerField("Max score", validators=[Optional(), NumberRange(min=0, max=100)])
    created_from = DateField("Created from", validators=[Optional()])
    created_to = DateField("Created to", validators=[Optional()])
    sort = SelectField(
        "Sort",
        choices=[
            ("created_at", "Created"),
            ("name", "Name"),
            ("company", "Company"),
            ("stage", "Stage"),
            ("score", "Score"),
            ("source", "Source"),
        ],
        default="created_at",
    )
    dir = SelectField("Direction", choices=[("desc", "Desc"), ("asc", "Asc")], default="desc")
    submit = SubmitField("Filter")


class QuickNoteForm(FlaskForm):
    content = TextAreaField("Note", validators=[Optional(), Length(max=5000)])
    submit = SubmitField("Add note")


class BulkActionForm(FlaskForm):
    action = SelectField(
        "Action",
        choices=[
            ("assign", "Assign"),
            ("change_stage", "Change stage"),
            ("archive", "Archive"),
            ("export", "Export"),
        ],
    )
    lead_ids = HiddenField()
    assigned_to = SelectField("Assign to", coerce=int, validators=[Optional()])
    stage_id = SelectField("Stage", coerce=int, validators=[Optional()])
    submit = SubmitField("Apply")
