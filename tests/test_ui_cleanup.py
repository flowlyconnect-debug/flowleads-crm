"""Customer-facing cleanup: listing sources are never shown, no AI score, UI in
Finnish, and static files are cached by the browser."""

import importlib.util
import pathlib

from app.analytics.prediction import PredictionService, signal_fi
from app.extensions import db
from app.leads.models import Lead
from app.listings import target_services as targets
from tests.test_targets import _listing, _login, _match_ids, _org, _profile


def test_new_catalogue_lead_has_no_source_url_or_source_tag(app):
    with app.app_context():
        org, _ = _org("src-notes")
        _profile(org)
        _listing(1, taloyhtio="As Oy Lähteetön", isannoitsija_email="a@example.com")
        targets.transfer_targets(org.id, _match_ids(org))
        lead = Lead.query.filter_by(organization_id=org.id).one()
        assert "http" not in (lead.notes or "")
        assert "oikotie" not in [t.lower() for t in lead.tags] and "import" not in [t.lower() for t in lead.tags]
        assert "Taloyhtiö: As Oy Lähteetön" in lead.notes


def test_lead_page_company_card_and_targets_hide_the_source(app, client):
    with app.app_context():
        org, user = _org("src-pages")
        _profile(org)
        listing = _listing(1, taloyhtio="As Oy Piilo", tulevat_remontit="Putket 2027")
        org_id, email, listing_id = org.id, user.email, listing.id
        targets.transfer_targets(org.id, _match_ids(org))
        lead = Lead.query.filter_by(organization_id=org.id).one()
        lead_id, source_ref = lead.id, lead.source_ref
        _listing(2, taloyhtio="As Oy Odottaa", tulevat_remontit="Katto 2028")

    _login(client, email)
    for path in (f"/leads/{lead_id}", f"/housing-companies/{listing_id}", "/targets"):
        html = client.get(path).get_data(as_text=True)
        assert "asunnot.oikotie.fi" not in html, path
        assert "Avaa lähde" not in html and "Avaa alkuperäinen ilmoitus" not in html, path
        assert "Lähteet (" not in html, path
    lead_html = client.get(f"/leads/{lead_id}").get_data(as_text=True)
    assert "Voittotodennäköisyys" not in lead_html
    assert "Ei pisteytystä" not in lead_html
    assert not source_ref or source_ref not in lead_html


def test_migration_strips_source_lines_and_tags():
    path = pathlib.Path(__file__).resolve().parent.parent / "migrations/versions/f1a2b3c4d5e6_hide_listing_sources_on_leads.py"
    spec = importlib.util.spec_from_file_location("hide_sources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    notes = "Taloyhtiö: As Oy A\nOikotie: https://asunnot.oikotie.fi/myytavat-asunnot/helsinki/1\nLähde: https://example.com/x"
    assert module._clean_notes(notes) == "Taloyhtiö: As Oy A"
    assert module._clean_notes("Lähde: kaverin vinkki") == "Lähde: kaverin vinkki"  # not a link: kept
    assert module._as_list('["Putkiremontti", "import", "Uusimaa"]') == ["Putkiremontti", "import", "Uusimaa"]


def test_prediction_texts_are_finnish():
    signals = {"has_proposal": False, "has_had_meeting": False, "days_since_last_contact": 12}
    risks = PredictionService._fallback_risk_signals(signals)
    assert risks == ["tarjousta ei ole vielä tehty", "ei yhteydenottoa 12 päivään", "tapaamista ei ole sovittu"]
    assert PredictionService._fallback_recommendation(signals).startswith("Ota liidiin uudelleen yhteyttä")
    # predictions stored earlier in English are shown in Finnish
    assert signal_fi("no meeting scheduled") == "tapaamista ei ole sovittu"
    assert signal_fi("no contact in 9 days") == "ei yhteydenottoa 9 päivään"
    assert signal_fi("Continue nurturing based on current pipeline stage") == "Jatka yhteydenpitoa myyntiputken vaiheen mukaisesti"
    assert signal_fi("Soita tällä viikolla") == "Soita tällä viikolla"


def test_status_labels_are_finnish(app, client):
    with app.app_context():
        org, user = _org("fi-labels", role="admin")
        db.session.commit()
        email = user.email
    _login(client, email)
    html = client.get("/proposals").get_data(as_text=True)
    assert ">Luonnos</option>" in html and ">Hyväksytty</option>" in html
    assert ">draft</option>" not in html
    assert app.jinja_env.filters["fi_label"]("follow_up") == "Seuranta"
    assert app.jinja_env.filters["fi_label"]("unknown_code") == "unknown_code"


def test_static_files_are_versioned_and_cached(app, client):
    html = client.get("/auth/login").get_data(as_text=True)
    assert "/static/css/main.css?v=" in html and "/static/css/design-system.css?v=" in html
    response = client.get("/static/css/main.css")
    assert "max-age=31536000" in response.headers.get("Cache-Control", "")
    response.close()
    assert "@import" not in pathlib.Path(app.static_folder, "css/main.css").read_text()


def test_compose_renders_template_hints_for_the_lead(app, client):
    from app.email.models import EmailTemplate

    with app.app_context():
        org, user = _org("compose-fi", role="admin")
        _profile(org)
        _listing(1, taloyhtio="As Oy Kirje", isannoitsija_email="manager@example.com")
        targets.transfer_targets(org.id, _match_ids(org))
        lead_id = Lead.query.filter_by(organization_id=org.id).one().id
        email = user.email
    _login(client, email)
    html = client.get(f"/leads/{lead_id}/email/compose").get_data(as_text=True)
    assert "'there'" not in html and '"there"' not in html
    assert "data-raw-subject" in html or "compose-template-card" not in html
    assert '"taloyhtio": "As Oy Kirje"' in html
