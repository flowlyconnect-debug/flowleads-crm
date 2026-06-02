from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    DecimalField,
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
    first_name = StringField("Etunimi", validators=[Optional(), Length(max=100)])
    last_name = StringField("Sukunimi", validators=[Optional(), Length(max=100)])
    email = StringField("Sähköposti", validators=[Optional(), Length(max=255)])
    phone = StringField("Puhelin", validators=[Optional(), Length(max=50)])
    company = StringField("Yritys", validators=[Optional(), Length(max=255)])
    title = StringField("Ammatti", validators=[Optional(), Length(max=150)])
    website = StringField("Kotisivu", validators=[Optional(), Length(max=500)])
    linkedin_url = StringField("LinkedIn", validators=[Optional(), Length(max=500)])
    stage_id = SelectField("Vaihe", coerce=int, validators=[Optional()])
    assigned_to = SelectField("Vastuuhenkilö", coerce=int, validators=[Optional()])
    source = SelectField(
        "Lähde",
        choices=[("manual", "Manuaalinen"), ("n8n", "n8n"), ("import", "Tuonti")],
        default="manual",
    )
    score_reason = TextAreaField("Pistemäärän perustelu", validators=[Optional()])
    notes = TextAreaField("Muistiinpanot", validators=[Optional()])
    tags = StringField("Tunnisteet (pilkuin eroteltuna)", validators=[Optional(), Length(max=1000)])
    score = IntegerField("Pisteet", validators=[Optional(), NumberRange(min=0, max=100)])
    deal_value = DecimalField("Kaupan arvo", places=2, validators=[Optional(), NumberRange(min=0)])
    source_ref = StringField("Lähteen viite", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Tallenna")


class LeadFilterForm(FlaskForm):
    search = StringField("Haku", validators=[Optional(), Length(max=255)])
    stage_id = SelectField("Vaihe", coerce=int, validators=[Optional()])
    source = SelectField(
        "Lähde",
        choices=[("", "Kaikki"), ("manual", "Manuaalinen"), ("n8n", "n8n"), ("import", "Tuonti")],
        validators=[Optional()],
    )
    assigned_to = SelectField("Vastuuhenkilö", coerce=int, validators=[Optional()])
    status = SelectField(
        "Tila",
        choices=[
            ("", "Ainoastaan aktiiviset"),
            ("archived", "Arkistoitu"),
            ("won", "Voitettu"),
            ("lost", "Hävitty"),
        ],
        validators=[Optional()],
    )
    score_min = IntegerField("Min. pisteet", validators=[Optional(), NumberRange(min=0, max=100)])
    score_max = IntegerField("Max. pisteet", validators=[Optional(), NumberRange(min=0, max=100)])
    created_from = DateField("Luotu alkaen", validators=[Optional()])
    created_to = DateField("Luotu asti", validators=[Optional()])
    gdpr_consent = SelectField(
        "GDPR-suostumus",
        choices=[("", "Kaikki"), ("1", "Suostuneet vain")],
        validators=[Optional()],
    )
    marketing_opt_in = SelectField(
        "Markkinointilupa",
        choices=[("", "Kaikki"), ("1", "Markkinointi: sallittu")],
        validators=[Optional()],
    )
    unsubscribed = SelectField(
        "Uutiskirjeestä pois",
        choices=[("", "Kaikki"), ("1", "Uutiskirjeestä pois")],
        validators=[Optional()],
    )
    is_anonymized = SelectField(
        "Anonymisoitu",
        choices=[("", "Kaikki"), ("1", "Anonymisoidut vain")],
        validators=[Optional()],
    )
    sort = SelectField(
        "Järjestä",
        choices=[
            ("created_at", "Luontiaika"),
            ("name", "Nimi"),
            ("company", "Yritys"),
            ("stage", "Vaihe"),
            ("score", "Pisteet"),
            ("last_activity", "Viimeisin aktiviteetti"),
            ("source", "Lähde"),
        ],
        default="created_at",
    )
    dir = SelectField("Suunta", choices=[("desc", "Laskeva"), ("asc", "Nouseva")], default="desc")
    submit = SubmitField("Suodata")


class QuickNoteForm(FlaskForm):
    content = TextAreaField("Muistiinpano", validators=[Optional(), Length(max=5000)])
    submit = SubmitField("Lisää muistiinpano")


class BulkActionForm(FlaskForm):
    action = SelectField(
        "Toiminto",
        choices=[
            ("assign", "Määritä vastuuhenkilö"),
            ("change_stage", "Vaihda vaihe"),
            ("archive", "Arkistoi"),
            ("export", "Vie"),
        ],
    )
    lead_ids = HiddenField()
    assigned_to = SelectField("Vastuuhenkilö", coerce=int, validators=[Optional()])
    stage_id = SelectField("Vaihe", coerce=int, validators=[Optional()])
    submit = SubmitField("Toteuta")
