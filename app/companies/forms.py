from flask_wtf import FlaskForm
from wtforms import SelectField, StringField
from wtforms.validators import DataRequired, Email, Length, Optional

from app.companies.services import COMPANY_TYPE_FILTERS


class CompanyForm(FlaskForm):
    name = StringField("Nimi", validators=[DataRequired(), Length(max=200)])
    type = SelectField(
        "Tyyppi",
        choices=[(value, label) for value, label in COMPANY_TYPE_FILTERS if value],
        default="prospect",
    )
    industry = StringField("Toimiala", validators=[Optional(), Length(max=100)])
    region = StringField("Alue", validators=[Optional(), Length(max=100)])


class ContactForm(FlaskForm):
    first_name = StringField("Etunimi", validators=[DataRequired(), Length(max=100)])
    last_name = StringField("Sukunimi", validators=[Optional(), Length(max=100)])
    email = StringField("Sähköposti", validators=[Optional(), Email(), Length(max=200)])
    phone = StringField("Puhelin", validators=[Optional(), Length(max=50)])
    title = StringField("Titteli", validators=[Optional(), Length(max=100)])
    company_id = SelectField("Yritys", coerce=int, validators=[Optional()])
