from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional


class RestoreBackupForm(FlaskForm):
    password = PasswordField("Salasana", validators=[DataRequired()])
    totp_code = StringField(
        "2FA-koodi",
        validators=[DataRequired(), Length(min=6, max=8)],
    )
    target_database_url = StringField(
        "Harjoituspalautuksen kohdetietokanta (tyhjä = palautus tuotantoon)",
        validators=[Optional(), Length(max=500)],
    )
    confirm_overwrite = BooleanField(
        "Ymmärrän, että nykyiset tiedot korvautuvat",
        validators=[DataRequired()],
    )
    submit = SubmitField("Palauta varmuuskopio")
