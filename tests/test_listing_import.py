"""Source-independent listing import: CSV / API rows become catalogue listings
and, when they name a renovation type, leads for matching profiles."""

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.listings.import_services import csv_template, import_listings, parse_listing_csv
from app.listings.models import Listing, ListingMatch
from app.listings.services import ListingServiceError, claim_analysis, save_analysis
from app.search.models import SearchProfile
from app.users.services import create_organization, create_user

import pytest

N8N_HEADERS = {"X-N8N-Secret": "test-n8n-secret"}


def _profile(slug="imp-org", *, rtype="Putkiremontti", regions=("Pirkanmaa",), **extra):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    profile = SearchProfile(
        organization_id=org.id,
        name="Profiili",
        remonttityyppi=rtype,
        regions=list(regions),
        source="oikotie",
        is_active=True,
        needs_rematch=False,
        daily_lead_limit=0,
        **extra,
    )
    db.session.add(profile)
    db.session.commit()
    return org, profile


def _row(**overrides):
    row = {
        "taloyhtio": "As Oy Esimerkkikatu 1",
        "osoite": "Esimerkkikatu 1",
        "kaupunki": "Tampere",
        "maakunta": "Pirkanmaa",
        "remonttityyppi": "Putkiremontti",
        "timing": "2027",
        "isannoitsija": "Isännöinti Esimerkki Oy",
        "isannoitsija_email": "isannointi@esimerkki.fi",
    }
    row.update(overrides)
    return row


def test_row_with_renovation_type_becomes_a_lead_immediately(app):
    with app.app_context():
        org, profile = _profile()
        result = import_listings([_row()])

        assert result["created"] == 1 and result["analysed"] == 1
        assert result["leads_created"] == 1 and result["errors"] == []
        listing = Listing.query.one()
        assert listing.source == "import"
        assert listing.analysis_status == "done"
        assert [(r.remonttityyppi, r.confidence, r.timing_year) for r in listing.renovations] == [
            ("Putkiremontti", 100, 2027)
        ]
        lead = Lead.query.filter_by(organization_id=org.id).one()
        assert lead.source == "import"
        assert lead.source_ref == listing.source_ref
        assert lead.company == "Isännöinti Esimerkki Oy"
        assert lead.email == "isannointi@esimerkki.fi"
        assert ListingMatch.query.filter_by(profile_id=profile.id).one().status == "lead_created"


def test_reimporting_the_same_row_updates_instead_of_duplicating(app):
    with app.app_context():
        org, _ = _profile()
        import_listings([_row()])
        again = import_listings([_row(isannoitsija_puh="040 123 4567")])

        assert again["created"] == 0 and again["updated"] == 1 and again["leads_created"] == 0
        assert Listing.query.count() == 1
        assert Lead.query.filter_by(organization_id=org.id).count() == 1


def test_profile_filters_apply_to_imported_listings(app):
    with app.app_context():
        org, _ = _profile(rtype="Lukkoremontti / kulunvalvonta", regions=("Uusimaa",))
        result = import_listings(
            [
                _row(),  # wrong type and region
                _row(taloyhtio="As Oy Lukko", osoite="Lukkotie 2", kaupunki="Espoo", maakunta="uusimaa",
                     remonttityyppi="lukkoremontti; Putkiremontti"),
            ]
        )
        assert result["analysed"] == 2 and result["leads_created"] == 1
        lead = Lead.query.filter_by(organization_id=org.id).one()
        assert "Lukkotie 2" in (lead.notes or "")


def test_new_profile_finds_earlier_imports(app):
    with app.app_context():
        import_listings([_row()])
        assert Lead.query.count() == 0

        from app.listings.services import materialize_pending_matches, rematch_profile

        org, profile = _profile()
        assert rematch_profile(profile) == 1
        materialize_pending_matches(profile_ids={profile.id})
        assert Lead.query.filter_by(organization_id=org.id).count() == 1


def test_text_only_row_waits_for_the_analyzer_without_a_page_fetch(app):
    with app.app_context():
        org, _ = _profile()
        text = "Yhtiökokous on päättänyt linjasaneerauksen hankesuunnittelusta, toteutus arviolta 2027."
        result = import_listings([_row(remonttityyppi="", description=text)])
        assert result["queued"] == 1 and result["analysed"] == 0 and Lead.query.count() == 0

        claimed = claim_analysis(limit=5)["listings"]
        assert len(claimed) == 1
        assert claimed[0]["fetch_detail"] is False
        assert claimed[0]["description"] == text
        assert claimed[0]["taloyhtio"] == "As Oy Esimerkkikatu 1"

        saved = save_analysis(
            claimed[0]["listing_id"],
            {"status": "done", "renovations": [{"remonttityyppi": "Putkiremontti", "confidence": 90, "timing": "2027"}]},
        )
        assert saved["leads_created"] == 1
        assert Lead.query.filter_by(organization_id=org.id).one().source == "import"


def test_oikotie_listings_still_ask_for_the_detail_page(app):
    with app.app_context():
        from app.listings.services import ingest_page

        ingest_page(maakunta="Pirkanmaa", mode="incremental", offset=0, page_in_run=1,
                    listings=[{"external_id": "555", "url": "https://asunnot.oikotie.fi/myytavat-asunnot/tampere/555"}])
        claimed = claim_analysis(limit=5)["listings"]
        assert claimed[0]["fetch_detail"] is True and claimed[0]["source"] == "oikotie"


def test_bad_rows_are_reported_and_good_rows_still_imported(app):
    with app.app_context():
        _profile()
        result = import_listings(
            [
                _row(maakunta="Narnia"),
                _row(taloyhtio="", osoite=""),
                _row(remonttityyppi="Uima-allasremontti"),
                _row(remonttityyppi="", description="liian lyhyt"),
                "not a dict",
                _row(taloyhtio="As Oy Kunnossa", osoite="Hyvätie 3"),
            ]
        )
        assert result["created"] == 1 and result["leads_created"] == 1
        assert [e["row"] for e in result["errors"]] == [1, 2, 3, 4, 5]
        assert "Narnia" in result["errors"][0]["error"]
        assert "Uima-allasremontti" in result["errors"][2]["error"]


def test_import_refuses_the_oikotie_source(app):
    with app.app_context():
        with pytest.raises(ListingServiceError):
            import_listings([_row()], source="oikotie")


def test_csv_semicolon_and_windows_encoding(app):
    data = (
        "Taloyhtiö;Osoite;Paikkakunta;Maakunta;Remonttityyppi;Ajankohta;Isännöitsijä\r\n"
        "As Oy Järvenpää;Järvitie 1;Jyväskylä;Keski-Suomi;Julkisivuremontti;2028;Isännöinti Äijälä\r\n"
        ";;;;;;\r\n"
    ).encode("cp1252")
    rows = parse_listing_csv(data)
    assert rows == [
        {
            "taloyhtio": "As Oy Järvenpää",
            "osoite": "Järvitie 1",
            "kaupunki": "Jyväskylä",
            "maakunta": "Keski-Suomi",
            "remonttityyppi": "Julkisivuremontti",
            "timing": "2028",
            "isannoitsija": "Isännöinti Äijälä",
        }
    ]


def test_csv_template_round_trips_through_the_importer(app):
    with app.app_context():
        _profile()
        rows = parse_listing_csv(("﻿" + csv_template()).encode("utf-8"))
        result = import_listings(rows)
        assert result["errors"] == []
        assert result["analysed"] == 1 and result["queued"] == 1 and result["leads_created"] == 1


@pytest.mark.parametrize(
    "data, message",
    [
        (b"", "tyhj"),
        (b"taloyhtio;osoite\nA;B\n", "maakunta"),
        (b"maakunta;kaupunki\nUusimaa;Espoo\n", "taloyhtio"),
        (b"maakunta;taloyhtio\n", "yht"),
    ],
)
def test_csv_structure_errors(data, message):
    with pytest.raises(ListingServiceError) as exc:
        parse_listing_csv(data)
    assert message in exc.value.message


def test_api_import_requires_the_n8n_secret_and_creates_leads(app, client):
    with app.app_context():
        org, _ = _profile()
        org_id = org.id

    body = {"source": "htj", "listings": [_row(external_id="1234567-8")]}
    assert client.post("/api/v1/n8n/listings/import", json=body).status_code in (401, 403)

    response = client.post("/api/v1/n8n/listings/import", json=body, headers=N8N_HEADERS)
    assert response.status_code == 200
    assert response.get_json()["leads_created"] == 1
    with app.app_context():
        listing = Listing.query.one()
        assert (listing.source, listing.external_id) == ("htj", "1234567-8")
        assert Lead.query.filter_by(organization_id=org_id).one().source_ref == "htj-1234567-8"


def test_superadmin_csv_upload(app, client):
    import io

    with app.app_context():
        org, _ = _profile()
        org_id = org.id
        admin = create_user("sa-import@test.com", "securepassword1", role="superadmin", organization_id=None)
        admin.totp_enabled = True
        db.session.commit()

    client.post("/auth/login", data={"email": "sa-import@test.com", "password": "securepassword1"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True

    template = client.get("/admin/catalogue/import-template.csv")
    assert template.status_code == 200 and b"maakunta" in template.data

    response = client.post(
        "/admin/catalogue/import",
        data={"file": (io.BytesIO(template.data), "kohteet.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Tuotu 2 uutta" in response.get_data(as_text=True)
    with app.app_context():
        assert Lead.query.filter_by(organization_id=org_id).count() == 1
