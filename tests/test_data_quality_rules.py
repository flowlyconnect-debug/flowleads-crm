"""Data quality rules found in the production data (18.11.2026): renovations
dated in an earlier year, the analyser's own notes in the evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.listings.housing import score_target
from app.listings.models import Listing, ListingRenovation
from app.listings.stages import clean_evidence, clean_old_evidence, possibly_done, too_old
from app.listings.target_services import why_line

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)
PIPE = "Putkiremontti"


def _r(year, evidence="PTS:ssä linjasaneeraus.", stage="planned"):
    return SimpleNamespace(remonttityyppi=PIPE, timing_year=year, timing=str(year) if year else None,
                           evidence=evidence, stage=stage, confidence=80, is_estimate=False)


@pytest.mark.parametrize(
    "text, cleaned",
    [
        ("Päätetty 2026-2027. Tarkista onko jo tehty.", "Päätetty 2026-2027."),
        ("Ikkunat 2027 (tarkistettava ajankohta). Lisäksi katto.", "Ikkunat 2027. Lisäksi katto."),
        ("Hallitus tarkistaa kuntotutkimuksen tulokset 2027.", "Hallitus tarkistaa kuntotutkimuksen tulokset 2027."),
        ("Tarkista onko jo tehty.", "Tarkista onko jo tehty."),  # nothing else left: kept as is
    ],
)
def test_clean_evidence(text, cleaned):
    assert clean_evidence(text) == cleaned


def test_evidence_is_cleaned_when_stored_and_in_old_rows(app):
    with app.app_context():
        listing = Listing(source="import", external_id="q1", maakunta="Uusimaa", analysis_status="done")
        listing.renovations.append(ListingRenovation(remonttityyppi=PIPE, confidence=80, evidence="Sukitus 2027. Tarkista onko jo tehty."))
        db.session.add(listing)
        db.session.commit()
        assert listing.renovations[0].evidence == "Sukitus 2027."
        # a row written before the cleaning (bypassing the model)
        db.session.execute(ListingRenovation.__table__.update().values(evidence="Katto 2026. Tarkista onko jo tehty."))
        db.session.commit()
        assert clean_old_evidence() == 1
        assert db.session.get(ListingRenovation, listing.renovations[0].id).evidence == "Katto 2026."


def test_an_earlier_year_may_be_done_unless_going_on():
    assert possibly_done(_r(2025), NOW)
    assert not possibly_done(_r(2025, "Linjasaneeraus alkoi 2025 ja jatkuu 2027."), NOW)
    assert not possibly_done(_r(2026), NOW) and not possibly_done(_r(None), NOW)
    assert too_old(_r(2024), NOW) and not too_old(_r(2025), NOW)
    assert not too_old(_r(2023, "Työt käynnissä."), NOW)


def test_score_and_reason_for_an_earlier_year(app):
    listing = Listing(isannoitsija_puh="040 1", isannoitsija_email="a@b.fi", first_seen_at=NOW)
    listing.renovations = [ListingRenovation(remonttityyppi=PIPE, confidence=80, evidence="PTS:ssä linjasaneeraus.",
                                             timing="2025", timing_year=2025)]
    timing = next(p for p in score_target([listing], {PIPE}, now=NOW)["parts"] if p["key"] == "timing")
    assert timing["points"] == 4 and "mahdollisesti jo tehty" in timing["detail"]
    contact = SimpleNamespace(isannoitsija_puh="", isannoitsija_email="")
    assert why_line([_r(2025)], contact).startswith(f"{PIPE} suunnitteilla, merkitty vuodelle 2025: mahdollisesti jo tehty")
    # a current one leads the sentence over an old one
    assert why_line([_r(2025), _r(2027)], contact).startswith(f"{PIPE} suunnitteilla, arviolta 2027")


@pytest.mark.parametrize(
    "raw, phone",
    [
        ("0401234567", "040 123 4567"),
        ("040 1234 567", "040 123 4567"),
        ("+358 40 123 4567", "040 123 4567"),
        ("puh. 050-123 4567", "050 123 4567"),
        ("09 1234567", "09 123 4567"),
        ("03 2345 678", "03 234 5678"),
        ("010 123 4567", "010 123 4567"),
        ("030 6000 123", "030 600 0123"),
        ("014 123 456", "014 123 456"),
        ("092 345 6789", "09 234 56789"),
        ("040 123 4567 / 050 765 4321", "040 123 4567"),
        ("+46 70 123 45 67", "+46701234567"),
        ("vaihde", "vaihde"),  # not a number: kept as written
        ("", None),
    ],
)
def test_normalize_phone(raw, phone):
    from app.listings.normalize import normalize_phone

    assert normalize_phone(raw) == phone


def test_normalize_email_and_manager_key():
    from app.listings.normalize import manager_key, normalize_email

    assert normalize_email(" Etu.Suku@Isannointi.FI ") == "etu.suku@isannointi.fi"
    assert normalize_email("mailto:info(at)isannointi.fi") == "info@isannointi.fi"
    assert normalize_email("ei sähköpostia") is None
    assert manager_key("Ehta Isännöinti Oy") == manager_key("Ehta isännöinti") == "ehta isännöinti"
    assert manager_key("REIM Hämeenlinna Oy") == manager_key("Reim Hämeenlinna")


def test_listing_contacts_are_normalized_when_saved_and_in_old_rows(app):
    from app.listings.normalize import normalize_old_contacts

    with app.app_context():
        listing = Listing(source="import", external_id="n1", maakunta="Uusimaa",
                          isannoitsija_puh="+358401234567", isannoitsija_email="INFO@ISANNOINTI.FI")
        db.session.add(listing)
        db.session.commit()
        assert (listing.isannoitsija_puh, listing.isannoitsija_email) == ("040 123 4567", "info@isannointi.fi")
        db.session.execute(Listing.__table__.update().values(isannoitsija_puh="0501112222"))
        db.session.commit()
        assert normalize_old_contacts() == 1
        assert db.session.get(Listing, listing.id).isannoitsija_puh == "050 111 2222"
        assert normalize_old_contacts() == 0  # marked: not done again


def test_backfill_stages_and_quality_page(app, client):
    from app.core.permissions import TWO_FA_SESSION_KEY
    from app.listings.quality import data_quality
    from app.listings.stages import backfill_stages
    from app.users.services import create_user

    with app.app_context():
        listing = Listing(source="import", external_id="b1", maakunta="Uusimaa", analysis_status="done")
        listing.renovations.append(ListingRenovation(remonttityyppi=PIPE, confidence=80, evidence="Yhtiökokous päätti putkiremontista."))
        db.session.add(listing)
        db.session.commit()
        db.session.execute(ListingRenovation.__table__.update().values(stage=None))
        db.session.commit()
        assert backfill_stages() == 1
        assert ListingRenovation.query.one().stage == "decided"
        quality = data_quality()
        decided = next(r for s in quality["sections"] for r in s["rows"] if r["label"] == "Päätetty")
        assert decided["count"] == 1 and decided["percent"] == 100
        root = create_user("root-laatu@test.com", "securepassword1", role="superadmin")
        root.totp_enabled = True
        db.session.commit()
    client.post("/auth/login", data={"email": "root-laatu@test.com", "password": "securepassword1"})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    page = client.get("/admin/data-quality").get_data(as_text=True)
    assert "Datan laatu" in page and "Kohteissa puhelin tai sähköposti" in page


def test_catalogue_health_tells_the_enricher_is_not_running(app):
    from app.listings import services as svc
    from app.listings.models import ListingMatch
    from app.search.models import SearchProfile
    from app.users.services import create_organization

    with app.app_context():
        org = create_organization("Laatu Oy", "laatu")
        db.session.flush()
        db.session.add(SearchProfile(organization_id=org.id, name="P", remonttityyppi=PIPE, regions=["Koko Suomi"],
                                     source="oikotie", is_active=True, min_confidence=50, needs_rematch=False))
        db.session.commit()
        listing = Listing(source="import", external_id="e1", maakunta="Uusimaa", analysis_status="done", taloyhtio="As Oy E",
                          first_seen_at=NOW, last_seen_at=NOW)
        listing.renovations.append(ListingRenovation(remonttityyppi=PIPE, confidence=80, evidence="PTS 2027.", timing_year=2027))
        db.session.add(listing)
        db.session.flush()
        svc.match_listing(listing)
        db.session.commit()
        assert listing.contact_status == "pending" and ListingMatch.query.count() == 1
        problems = svc.catalogue_health()["problems"]
        assert any("Yhteystietohaku (Enricher)" in p and "(ei koskaan)" in p for p in problems)
