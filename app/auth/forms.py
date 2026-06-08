from flask_wtf import FlaskForm
from wtforms import EmailField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Optional


class LoginForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=1, max=128)])
    submit = SubmitField("Sign in")


class RegisterForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=12, max=128)])
    password_confirm = PasswordField(
        "Confirm password", validators=[DataRequired(), Length(min=12, max=128)]
    )
    submit = SubmitField("Register")


class ResetPasswordRequestForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    submit = SubmitField("Send reset link")


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), Length(min=12, max=128)])
    password_confirm = PasswordField(
        "Confirm password", validators=[DataRequired(), Length(min=12, max=128)]
    )
    submit = SubmitField("Reset password")


class TwoFASetupForm(FlaskForm):
    token = StringField("Verification code", validators=[DataRequired(), Length(min=6, max=8)])
    submit = SubmitField("Enable 2FA")


class TwoFAVerifyForm(FlaskForm):
    token = StringField("Authentication code", validators=[DataRequired(), Length(min=6, max=8)])
    submit = SubmitField("Verify")
