from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional


class RestoreBackupForm(FlaskForm):
    password = PasswordField("Password", validators=[DataRequired()])
    totp_code = StringField(
        "2FA code",
        validators=[DataRequired(), Length(min=6, max=8)],
    )
    target_database_url = StringField(
        "Restore drill target database URL (leave empty to restore into production)",
        validators=[Optional(), Length(max=500)],
    )
    confirm_overwrite = BooleanField(
        "I understand this will overwrite current data",
        validators=[DataRequired()],
    )
    submit = SubmitField("Restore backup")
