from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, URL

from app.webhooks.models import WEBHOOK_PROVIDERS


class WebhookEndpointForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    provider = SelectField(
        "Provider",
        validators=[DataRequired()],
        choices=[(provider, provider.capitalize()) for provider in WEBHOOK_PROVIDERS],
    )
    url = StringField("URL", validators=[DataRequired(), URL(), Length(max=2000)])
    secret = StringField("Secret", validators=[Optional(), Length(max=1000)])
    is_active = BooleanField("Active", default=True)
    submit = SubmitField("Save")

