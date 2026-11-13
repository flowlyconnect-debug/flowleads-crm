"""Board chair, maintenance company and property management offices."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.leads.property_info import get_property_info_rows
from app.listings import contacts
from app.listings import insight_services as insights
from app.listings import services as svc
from app.listings import target_services as targets
from app.listings.import_services import import_listings, parse_listing_csv
from app.listings.models import Listing, ListingMatch, PropertyManagerOffice
from app.search.models import SearchProfile
from app.users.models import User
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
PIPE = "Putkiremontti"
NOW = datetime.now(timezone.utc)
N8N = {"X-N8N-Secret": "test-n8n-secret"}

HEADER = (
    "taloyhtio;osoite;kaupunki;maakunta;remonttityyppi;ajankohta;isannointitoimisto;yhteyshenkilo;"
    "isannoitsija_puh;hallituksen_puheenjohtaja;hallituksen_pj_puh;hallituksen_pj_email;huoltoyhtio;"
    "huoltoyhtio_puh;huoltoyhtio_email;isannointitoimisto_ytunnus;isannointitoimisto_osoite;"
    "isannointitoimisto_puh;isannointitoimisto_www;taloyhtion_ytunnus"
)


def _org(slug):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    user.totp_enabled = True
    db.session.add(
        SearchProfile(
            organization_id=org.id,
            name="P",
            remonttityyppi=PIPE,
            regions=["Uusimaa"],
            source="oikotie",
            is_active=True,
            min_confidence=50,
            needs_rematch=False,
        )
    )
    db.session.commit()
    return org, user


def _import(*rows):
    data = ("﻿" + "\n".join([HEADER, *rows])).encode("utf-8")
    return import_listings(parse_listing_csv(data))


ROW = (
    "As Oy Koivikko;Koivutie 1;Espoo;Uusimaa;Putkiremontti;2027;Isännöinti Kallio Oy;Hanna Isännöitsijä;"
    "040 111;Pekka Puheenjohtaja;050 222;pekka@koivikko.fi;Huolto Ässä Oy;010 333;huolto@assa.fi;"
    "1234567-8;Kalliotie 5, 02100 Espoo;09 444;kallio.fi;7654321-0"
)


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def test_office_key_ignores_case_spacing_and_legal_form():
    assert contacts.office_key("Isännöinti Kallio Oy") == contacts.office_key("  ISÄNNÖINTI  kallio ")
    assert contacts.office_key("Retta Isännöinti Ab") == "retta isannointi"
    assert contacts.office_key("") == ""


def test_import_stores_people_and_office(app):
    with app.app_context():
        org, _ = _org("people")
        result = _import(ROW)
        assert result["created"] == 1 and not result["errors"]
        listing = Listing.query.one()
        assert (listing.hallitus_pj, listing.hallitus_pj_puh, listing.hallitus_pj_email) == (
            "Pekka Puheenjohtaja",
            "050 222",
            "pekka@koivikko.fi",
        )
        assert (listing.huoltoyhtio, listing.huoltoyhtio_puh, listing.huoltoyhtio_email) == (
            "Huolto Ässä Oy",
            "010 333",
            "huolto@assa.fi",
        )
        assert listing.isannoitsija_henkilo == "Hanna Isännöitsijä"
        office = PropertyManagerOffice.query.one()
        assert office.name == "Isännöinti Kallio Oy" and office.ytunnus == "1234567-8"
        assert office.puhelin == "09 444" and office.website == "https://kallio.fi"

        # importing again without the office columns never erases them
        _import("As Oy Toinen;Tie 2;Espoo;Uusimaa;Putkiremontti;2028;isännöinti kallio;;;;;;;;;;;;;")
        office = PropertyManagerOffice.query.one()
        assert office.ytunnus == "1234567-8" and office.puhelin == "09 444"


def test_bad_emails_are_dropped_not_fatal(app):
    with app.app_context():
        _org("bademail")
        result = _import(ROW.replace("pekka@koivikko.fi", "pekka(at)koivikko"))
        assert result["created"] == 1
        assert Listing.query.one().hallitus_pj_email is None


def test_lead_carries_the_other_contacts(app):
    with app.app_context():
        org, _ = _org("leadpeople")
        _import(ROW)
        match = ListingMatch.query.filter_by(organization_id=org.id).one()
        result = targets.transfer_targets(org.id, [match.id])
        lead = db.session.get(Lead, result["lead_ids"][0])
        meta = lead.ai_contact_info
        assert meta["hallitus_pj"] == "Pekka Puheenjohtaja" and meta["huoltoyhtio_puh"] == "010 333"
        assert meta["isannointitoimisto_ytunnus"] == "1234567-8"
        assert "Hallituksen puheenjohtaja: Pekka Puheenjohtaja, 050 222, pekka@koivikko.fi" in lead.notes
        labels = {row["label"]: row for row in get_property_info_rows(meta)}
        assert labels["Taloyhtiön Y-tunnus"]["value"] == "7654321-0"
        assert labels["Hallituksen puheenjohtaja"]["value"] == "Pekka Puheenjohtaja"
        assert labels["Isännöintitoimiston verkkosivut"]["is_url"] is True


def test_merged_listing_fills_missing_contacts_on_the_lead(app):
    with app.app_context():
        org, _ = _org("mergefill")
        _import("As Oy Koivikko;Koivutie 1;Espoo;Uusimaa;Putkiremontti;2027;;;;;;;;;;;;;;")
        match = ListingMatch.query.filter_by(organization_id=org.id).one()
        lead = db.session.get(Lead, targets.transfer_targets(org.id, [match.id])["lead_ids"][0])
        assert not (lead.ai_contact_info or {}).get("hallitus_pj")
        # another listing of the same company arrives later with the chair
        _import(ROW.replace("Koivutie 1", "Koivutie 3"))
        assert svc.merge_into_existing_leads() == 1
        db.session.refresh(lead)
        assert lead.ai_contact_info["hallitus_pj"] == "Pekka Puheenjohtaja"
        assert lead.phone == "040 111"


def test_contact_filter_and_score_count_the_board_chair(app):
    with app.app_context():
        org, _ = _org("chairfilter")
        _import(
            "As Oy Hiljainen;Tie 9;Espoo;Uusimaa;Putkiremontti;2027;;;;Paula Pj;050 999;;;;;;;;;"
        )
        assert targets.list_targets(org.id, filters={"contact": "1"}).total == 1
        row = targets.target_rows(org.id, targets.list_targets(org.id).items)[0]
        assert row["people"]["chair"]["phone"] == "050 999"
        contact = next(p for p in row["score_parts"] if p["key"] == "contact")
        assert contact["points"] == 10


def test_office_csv_import_and_api(client, app):
    with app.app_context():
        data = (
            "﻿isannointitoimisto;y_tunnus;osoite;postinumero;kaupunki;puhelin;sahkoposti;www\n"
            "Retta Isännöinti Oy;0194158-2;Mannerheimintie 1;00100;Helsinki;010 123;info@retta.fi;retta.fi\n"
            ";;;;;;;\n"
            "Ilman Nimeä;;;;;;;\n"
        ).encode("utf-8")
        rows = contacts.parse_office_csv(data)
        result = contacts.import_offices(rows)
        assert result["created"] == 2 and result["errors"] == []
        office = contacts.office_for("RETTA ISÄNNÖINTI")
        assert office.postinumero == "00100" and office.email == "info@retta.fi"
        assert contacts.office_dict(office)["osoite"] == "Mannerheimintie 1, 00100 Helsinki"

    response = client.post(
        "/api/v1/n8n/listings/managers/import",
        json={"offices": [{"name": "Retta Isännöinti Oy", "puhelin": "010 999"}, {"name": ""}]},
        headers=N8N,
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["updated"] == 1 and len(body["errors"]) == 1
    assert client.post("/api/v1/n8n/listings/managers/import", json={"offices": []}).status_code in (401, 403)
    with app.app_context():
        assert contacts.office_for("Retta Isännöinti Oy").puhelin == "010 999"


def test_analysis_saves_manager_person_and_maintenance(app):
    with app.app_context():
        listing = Listing(source="oikotie", external_id="9", maakunta="Uusimaa", first_seen_at=NOW, last_seen_at=NOW)
        db.session.add(listing)
        db.session.commit()
        svc.save_analysis(
            listing.id,
            {
                "status": "done",
                "isannoitsija": "Isännöinti Oy",
                "isannoitsija_henkilo": "Ilkka Isännöitsijä",
                "huoltoyhtio": "Huolto Oy",
                "huoltoyhtio_puh": "010 1",
                "renovations": [],
            },
        )
        listing = db.session.get(Listing, listing.id)
        assert (listing.isannoitsija_henkilo, listing.huoltoyhtio, listing.huoltoyhtio_puh) == (
            "Ilkka Isännöitsijä",
            "Huolto Oy",
            "010 1",
        )


def test_pages_show_people_and_office(client, app):
    with app.app_context():
        org, user = _org("pagespeople")
        _import(ROW)
        listing_id = Listing.query.one().id
        email = user.email
    _login(client, email)
    page = client.get("/targets").get_data(as_text=True)
    assert "Hallituksen pj: Pekka Puheenjohtaja" in page and "Huolto: Huolto Ässä Oy" in page
    card = client.get(f"/housing-companies/{listing_id}").get_data(as_text=True)
    assert "Hallitus ja huolto" in card and "050 222" in card and "huolto@assa.fi" in card
    assert "Toimiston Y-tunnus" in card and "1234567-8" in card and "Kalliotie 5, 02100 Espoo" in card
    manager = client.get("/managers/view?name=Isännöinti Kallio Oy").get_data(as_text=True)
    assert "Y-tunnus 1234567-8" in manager and "09 444" in manager


def test_admin_office_import_page(client, app):
    with app.app_context():
        admin = create_user("super-offices@test.com", PASSWORD, role="superadmin")
        admin.totp_enabled = True
        db.session.commit()
    _login(client, "super-offices@test.com")
    page = client.get("/admin/catalogue")
    assert page.status_code == 200 and "Isännöintitoimistot (0)" in page.get_data(as_text=True)
    template = client.get("/admin/catalogue/offices/import-template.csv")
    assert template.status_code == 200 and "isannointitoimisto;y_tunnus" in template.get_data(as_text=True)
    from io import BytesIO

    response = client.post(
        "/admin/catalogue/offices/import",
        data={"file": (BytesIO("isannointitoimisto;puhelin\nToimisto Oy;010\n".encode()), "t.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "1 uutta" in response.get_data(as_text=True)
    with app.app_context():
        assert PropertyManagerOffice.query.count() == 1
        assert User.query.filter_by(email="super-offices@test.com").one()
