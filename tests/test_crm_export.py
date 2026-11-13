"""Leads to HubSpot and Pipedrive: import files and API export (mocked HTTP)."""

from __future__ import annotations

import csv
import io
import json

import pytest

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.integrations import services as crm
from app.integrations.models import CrmConnection, CrmExportLink
from app.leads.models import Lead
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = json.dumps(self._payload).encode()
        self.text = self.content.decode()

    def json(self):
        return self._payload


class FakeHttp:
    """Answers like HubSpot / Pipedrive and records every request."""

    def __init__(self, fail_on=None, status=400):
        self.calls = []
        self.next_id = 100
        self.fail_on = fail_on
        self.status = status

    def request(self, method, url, timeout=None, headers=None, json=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "json": json})
        if self.fail_on and self.fail_on in url:
            return FakeResponse(self.status, {"message": "nope"})
        if url.endswith("/users/me"):
            return FakeResponse(200, {"data": {"company_name": "Urakka Oy"}})
        if method == "POST":
            self.next_id += 1
            if "pipedrive" in url:
                return FakeResponse(201, {"data": {"id": self.next_id}})
            return FakeResponse(201, {"id": str(self.next_id)})
        return FakeResponse(200, {})


def _org(slug):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    admin = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    admin.totp_enabled = True
    seller = create_user(f"user-{slug}@test.com", PASSWORD, role="user", organization_id=org.id)
    db.session.commit()
    return org, admin, seller


def _lead(org, **fields):
    from app.api.services import upsert_lead

    payload = {
        "company": "As Oy Koivikko",
        "title": "Isännöitsijä: Isännöinti Kallio Oy, Hanna Isännöitsijä",
        "phone": "040 111",
        "email": "hanna@kallio.fi",
        "source": "import",
        "source_ref": f"import-{fields.pop('ref', 'k1')}",
        "metadata": {
            "taloyhtio": "As Oy Koivikko",
            "isannoitsija_yritys": "Isännöinti Kallio Oy",
            "isannoitsija_henkilo": "Hanna Isännöitsijä",
            "remonttityyppi": "Putkiremontti",
            "remontin_ajankohta": "2027",
            "osoite": "Koivutie 1",
            "kaupunki": "Espoo",
            "hallitus_pj": "Pekka Pj",
        },
    }
    payload.update(fields)
    lead, _ = upsert_lead(org.id, payload, match_by_email=False)
    db.session.commit()
    return lead


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def test_record_maps_a_housing_company_lead(app):
    with app.app_context():
        org, _, _ = _org("record")
        record = crm.lead_record(_lead(org))
        assert record["company"] == "As Oy Koivikko"
        assert (record["first_name"], record["last_name"]) == ("Hanna", "Isännöitsijä")
        assert record["deal_name"] == "As Oy Koivikko – Putkiremontti"
        assert record["job_title"] == "Isännöitsijä"
        assert "Remontti: Putkiremontti (2027)" in record["summary"] and "Pekka Pj" in record["summary"]


def test_import_files_have_the_crms_column_names(app):
    with app.app_context():
        org, _, _ = _org("files")
        lead = _lead(org, ref="k1")
        _lead(org, ref="k2", company="=HYPERLINK(\"x\")")
        hubspot = list(csv.reader(io.StringIO(crm.import_file("hubspot", Lead.query.filter_by(organization_id=org.id).all()))))
        assert hubspot[0][:3] == ["Company name", "Company street address", "Company city"]
        assert hubspot[1][0] == "As Oy Koivikko" and hubspot[1][3] == "Hanna"
        # spreadsheet formulas are neutralised
        assert not hubspot[2][0].startswith("=")
        pipedrive = list(csv.reader(io.StringIO(crm.import_file("pipedrive", [lead]))))
        assert pipedrive[0][0] == "Organization - Name" and pipedrive[1][7] == "EUR"
        with pytest.raises(crm.CrmExportError):
            crm.import_file("salesforce", [lead])


def test_connect_checks_and_encrypts_the_token(app):
    with app.app_context():
        org, admin, _ = _org("connect")
        http = FakeHttp()
        connection = crm.connect(org.id, "pipedrive", "abcdef1234567890abcdef", user_id=admin.id, session=http)
        db.session.commit()
        assert connection.account_label == "Urakka Oy"
        assert "abcdef1234567890abcdef" not in connection.token_encrypted
        assert http.calls[0]["headers"]["x-api-token"] == "abcdef1234567890abcdef"
        assert "api_token" not in http.calls[0]["url"]  # never in the URL
        with pytest.raises(crm.CrmExportError) as bad:
            crm.connect(org.id, "hubspot", "pat-123456789", user_id=admin.id, session=FakeHttp(fail_on="hubapi", status=401))
        assert bad.value.message == "HubSpot rejected the API token."
        with pytest.raises(crm.CrmExportError):
            crm.connect(org.id, "hubspot", "short", user_id=admin.id, session=FakeHttp())


def test_hubspot_export_creates_then_updates(app):
    with app.app_context():
        org, admin, _ = _org("hubspot")
        crm.connect(org.id, "hubspot", "pat-eu1-1234567890", user_id=admin.id, session=FakeHttp())
        db.session.commit()
        lead = _lead(org)
        http = FakeHttp()
        result = crm.push_leads(org.id, "hubspot", [lead], session=http)
        assert result == {"created": 1, "updated": 0, "failed": 0, "errors": []}
        posts = [c for c in http.calls if c["method"] == "POST"]
        assert [c["url"].rsplit("/", 1)[-1] for c in posts] == ["companies", "contacts", "deals"]
        deal = posts[2]["json"]["properties"]
        assert deal["dealname"] == "As Oy Koivikko – Putkiremontti"
        assert (deal["pipeline"], deal["dealstage"]) == ("default", "appointmentscheduled")
        assert http.calls[0]["headers"]["Authorization"] == "Bearer pat-eu1-1234567890"
        assert sum(1 for c in http.calls if "associations/default" in c["url"]) == 3
        link = CrmExportLink.query.filter_by(lead_id=lead.id, provider="hubspot").one()
        assert (link.company_id, link.contact_id, link.deal_id) == ("101", "102", "103")
        assert any("Viety HubSpotiin" in (a.content or "") for a in lead.activities)

        # second export updates the same objects
        http = FakeHttp()
        assert crm.push_leads(org.id, "hubspot", [lead], session=http)["updated"] == 1
        assert {c["method"] for c in http.calls if "/crm/v3/" in c["url"]} == {"PATCH"}
        assert "pipeline" not in http.calls[2]["json"]["properties"]


def test_pipedrive_export_and_partial_failure(app):
    with app.app_context():
        org, admin, _ = _org("pipedrive")
        crm.connect(org.id, "pipedrive", "abcdef1234567890abcdef", user_id=admin.id, session=FakeHttp())
        db.session.commit()
        lead = _lead(org)
        http = FakeHttp()
        assert crm.push_leads(org.id, "pipedrive", [lead], session=http)["created"] == 1
        kinds = [c["url"].rsplit("/", 1)[-1] for c in http.calls if c["method"] == "POST"]
        assert kinds == ["organizations", "persons", "deals", "notes"]
        person = http.calls[1]["json"]
        assert person["org_id"] == 101 and person["email"][0]["value"] == "hanna@kallio.fi"

        other = _lead(org, ref="k2", company="As Oy Toinen")
        result = crm.push_leads(org.id, "pipedrive", [other], session=FakeHttp(fail_on="/deals"))
        assert result["failed"] == 1 and "Pipedrive refused the data" in result["errors"][0]["error"]
        # the organization and person created before the failure are remembered
        link = CrmExportLink.query.filter_by(lead_id=other.id).one()
        assert link.company_id and link.deal_id is None
        assert CrmConnection.query.filter_by(organization_id=org.id).one().last_error


def test_export_needs_a_connection_and_own_leads(app):
    with app.app_context():
        org, admin, _ = _org("noconn")
        other, other_admin, _ = _org("noconn2")
        lead = _lead(org)
        with pytest.raises(crm.CrmExportError) as missing:
            crm.push_leads(org.id, "hubspot", [lead], session=FakeHttp())
        assert missing.value.message == "HubSpot is not connected."
        crm.connect(other.id, "hubspot", "pat-eu1-1234567890", user_id=other_admin.id, session=FakeHttp())
        db.session.commit()
        http = FakeHttp()
        assert crm.push_leads(other.id, "hubspot", [lead], session=http)["created"] == 0
        assert http.calls == []


def test_settings_and_export_pages(client, app, monkeypatch):
    with app.app_context():
        org, admin, seller = _org("pages")
        lead_id = _lead(org).id
        admin_email, seller_email = admin.email, seller.email

    monkeypatch.setattr(crm.requests, "Session", lambda: FakeHttp())
    _login(client, seller_email)
    assert client.get("/settings/integrations").status_code in (302, 403)
    response = client.get("/leads/export/hubspot.csv")
    assert response.status_code == 200 and "Company name" in response.get_data(as_text=True)
    assert "As Oy Koivikko" in client.get(f"/leads/export/pipedrive.csv?ids={lead_id}").get_data(as_text=True)
    assert client.get("/leads/export/salesforce.csv").status_code == 404
    client.get("/auth/logout")

    _login(client, admin_email)
    page = client.get("/settings/integrations").get_data(as_text=True)
    assert "HubSpot" in page and "Pipedrive" in page and "Ei yhdistetty" in page
    response = client.post("/settings/integrations/hubspot/connect", data={"token": "pat-eu1-1234567890"}, follow_redirects=True)
    assert "HubSpot on yhdistetty" in response.get_data(as_text=True)
    detail = client.get(f"/leads/{lead_id}").get_data(as_text=True)
    assert "Vie HubSpotiin" in detail
    pushed = client.post(f"/leads/{lead_id}/crm/hubspot", follow_redirects=True).get_data(as_text=True)
    assert "Viety HubSpotiin: 1 uutta" in pushed and "Päivitä HubSpotiin" in pushed
    bulk = client.post("/leads/crm/hubspot", data={"lead_ids": json.dumps([str(lead_id)])}, follow_redirects=True)
    assert "1 päivitetty" in bulk.get_data(as_text=True)
    listing = client.get("/leads").get_data(as_text=True)
    assert "HubSpot-tuontitiedosto" in listing and "Vie HubSpotiin" in listing
    client.post("/settings/integrations/hubspot/disconnect")
    with app.app_context():
        assert CrmConnection.query.count() == 0
