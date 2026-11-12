"""Kohteet search in plain Finnish (app/listings/nl_search.py)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.listings import services as svc
from app.listings.models import Listing, ListingRenovation
from app.listings.nl_search import interpret, parse_query
from app.search.models import SearchProfile
from app.users.services import create_organization, create_user

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)
PASSWORD = "securepassword1"
PIPE = "Putkiremontti"
ROOF = "Katto- ja vesikattoremontti"
OPTIONS = {
    "renovations": [PIPE, ROOF],
    "cities": ["Tampere", "Turku", "Helsinki", "Espoo", "Seinäjoki", "Mänttä-Vilppula"],
    "buildings": ["Kerrostalo", "Rivitalo"],
}


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "putkiremontit Tampereella 2027 mennessä kerrostaloissa, joilla on yhteystiedot",
            {"renovation": PIPE, "city": "Tampere", "year": "2027", "building": "Kerrostalo", "contact": "1"},
        ),
        ("kattoremontteja Turussa ensi vuonna", {"renovation": ROOF, "city": "Turku", "year": "2027"}),
        ("Helsingissä uusimmat", {"city": "Helsinki", "sort": "newest"}),
        ("rivitalot Uudellamaalla", {"building": "Rivitalo", "region": "Uusimaa"}),
        ("Pohjois-Pohjanmaalla putket", {"region": "Pohjois-Pohjanmaa", "renovation": PIPE}),
        ("Pohjanmaalla katot", {"region": "Pohjanmaa", "renovation": ROOF}),
        ("Keski-Suomessa tänä vuonna", {"region": "Keski-Suomi", "year": "2026"}),
        ("Seinäjoella lähivuosina", {"city": "Seinäjoki", "year": "2028"}),
        ("Mänttä-Vilppulassa varmimmat", {"city": "Mänttä-Vilppula", "sort": "confidence"}),
        ("linjasaneeraus ennen 2028 sähköposti", {"renovation": PIPE, "year": "2027", "contact": "1"}),
        ("putket 2020 mennessä", {"renovation": PIPE, "year": "2026"}),  # never a past year
    ],
)
def test_parse_reads_finnish(text, expected):
    assert parse_query(text, OPTIONS, now=NOW)["params"] == expected


def test_parse_reports_what_it_did_not_understand():
    result = parse_query("julkisivut Espoossa ja tähtitieteelliset", OPTIONS, now=NOW)
    assert result["params"] == {"city": "Espoo"}
    assert "tahtitieteelliset" in result["unknown"]
    assert "Julkisivuremontti ei ole urakoissasi" in result["unknown"]
    # e-mail is a contact wish, not an electrical renovation
    assert parse_query("sähköposti", OPTIONS, now=NOW)["params"] == {"contact": "1"}


def test_parse_falls_back_to_name_search():
    result = parse_query("As Oy Kallio", OPTIONS, now=NOW)
    assert result == {"params": {}, "recognized": [], "unknown": [], "search": "As Oy Kallio"}
    assert parse_query("", OPTIONS, now=NOW)["search"] == ""


class _FakeAI:
    def __init__(self, answer):
        self.answer = answer
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.prompts.append(kwargs["messages"][-1]["content"])
        if isinstance(self.answer, Exception):
            raise self.answer
        content = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_ai_answer_is_limited_to_offered_values(app):
    with app.app_context():
        fake = _FakeAI({"renovation": PIPE, "city": "Pariisi", "region": "Pirkanmaa", "year": 2028, "contact": True, "sort": "DROP TABLE"})
        result = interpret("putkia pirkanmaalla", OPTIONS, now=NOW, client=fake)
        assert result["source"] == "ai"
        assert result["params"] == {"renovation": PIPE, "region": "Pirkanmaa", "year": "2028", "contact": "1"}
        assert "Tampere" in fake.prompts[0]  # the allowed values were given
        # a failing or useless model answer falls back to the rules
        assert interpret("putket Turussa", OPTIONS, now=NOW, client=_FakeAI(RuntimeError("down")))["source"] == "rules"
        assert interpret("putket Turussa", OPTIONS, now=NOW, client=_FakeAI("not json"))["params"] == {
            "renovation": PIPE,
            "city": "Turku",
        }
        # without the switch and a key the model is never asked
        assert interpret("putket", OPTIONS, now=NOW)["source"] == "rules"


def test_ask_route_applies_filters(client, app):
    with app.app_context():
        org = create_organization("Org ask", "ask")
        db.session.flush()
        user = create_user("admin-ask@test.com", PASSWORD, role="admin", organization_id=org.id)
        user.totp_enabled = True
        db.session.add(
            SearchProfile(organization_id=org.id, name="P", remonttityyppi=PIPE, regions=["Uusimaa", "Pirkanmaa"],
                          source="oikotie", is_active=True, min_confidence=50, needs_rematch=False)
        )
        db.session.commit()
        for ext, city, region in ((1, "Tampere", "Pirkanmaa"), (2, "Espoo", "Uusimaa")):
            listing = Listing(source="import", external_id=str(ext), maakunta=region, kaupunki=city,
                              taloyhtio=f"As Oy Haku {city}", analysis_status="done", published_at=NOW)
            listing.renovations.append(ListingRenovation(remonttityyppi=PIPE, confidence=80, timing="2027", timing_year=2027))
            db.session.add(listing)
            db.session.flush()
            svc.match_listing(listing)
        db.session.commit()
    client.post("/auth/login", data={"email": "admin-ask@test.com", "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True

    response = client.get("/targets/ask?q=putkiremontit+Tampereella")
    assert response.status_code == 302
    assert "city=Tampere" in response.location and "renovation=Putkiremontti" in response.location
    page = client.get(response.location).get_data(as_text=True)
    assert "As Oy Haku Tampere" in page and "As Oy Haku Espoo" not in page
    assert "Tulkittiin:" in page and "Kerro mitä etsit" in page
    # shown once
    assert "Tulkittiin:" not in client.get(response.location).get_data(as_text=True)
    by_name = client.get("/targets/ask?q=As+Oy+Haku+Espoo", follow_redirects=True).get_data(as_text=True)
    assert "As Oy Haku Espoo" in by_name and "As Oy Haku Tampere" not in by_name
    assert client.get("/targets/ask?q=").status_code == 302
