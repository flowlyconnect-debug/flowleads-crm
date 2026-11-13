"""The renovation timeline (PTS strip) on Kohteet, the dashboard and a lead,
and the Kohteet sort "Remontti alkaa ensin"."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.listings.pts import pts_from_text, pts_strip, renovation_mark

NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)


def _r(rtype, year):
    return SimpleNamespace(remonttityyppi=rtype, timing_year=year)


def test_strip_has_five_years_from_this_year_and_marks_start_years():
    pts = pts_strip([_r("Putkiremontti", 2027), _r("Julkisivuremontti", 2029)], now=NOW)
    assert [cell["year"] for cell in pts["years"]] == [2026, 2027, 2028, 2029, 2030]
    assert [m["abbr"] for m in pts["years"][1]["marks"]] == ["Pu"]
    assert [m["abbr"] for m in pts["years"][3]["marks"]] == ["Ju"]
    assert pts["start_year"] == 2027
    assert pts["open"] == []


def test_past_year_is_due_now_and_unknown_or_far_years_are_listed_apart():
    pts = pts_strip([_r("Katto- ja vesikattoremontti", 2024), _r("Putkiremontti", None), _r("Julkisivuremontti", 2034)], now=NOW)
    assert [m["abbr"] for m in pts["years"][0]["marks"]] == ["Ka"]
    assert pts["start_year"] == 2026
    assert [(m["short"], m.get("year")) for m in pts["open"]] == [("Putket", None), ("Julkisivu", 2034)]


def test_same_type_and_year_is_marked_once():
    pts = pts_strip([_r("Putkiremontti", 2027), _r("Putkiremontti", 2027)], now=NOW)
    assert len(pts["years"][1]["marks"]) == 1


def test_every_renovation_type_has_a_distinct_mark():
    from app.search.constants import REMONTTITYYPIT

    marks = [renovation_mark(t)["abbr"] for t in REMONTTITYYPIT]
    assert len(set(marks)) == len(marks)


def test_strip_from_lead_text():
    pts = pts_from_text("Putkiremontti", "kevät 2028", now=NOW)
    assert [m["abbr"] for m in pts["years"][2]["marks"]] == ["Pu"]
    assert pts_from_text(None, None, now=NOW)["start_year"] is None


def test_year_sort_orders_targets_by_first_renovation_year(app):
    from app.extensions import db
    from app.listings import services as svc
    from app.listings import target_services as targets
    from app.listings.models import Listing, ListingRenovation
    from app.search.models import SearchProfile
    from app.users.services import create_organization

    with app.app_context():
        org = create_organization("Org pts", "pts")
        db.session.flush()
        db.session.add(SearchProfile(organization_id=org.id, name="P", remonttityyppi="Putkiremontti", regions=["Uusimaa"],
                                     source="oikotie", is_active=True, min_confidence=60, needs_rematch=False))
        db.session.flush()
        now = datetime.now(timezone.utc)
        for ext, year in (("a", 2029), ("b", 2027), ("c", None)):
            listing = Listing(source="oikotie", external_id=ext, maakunta="Uusimaa", taloyhtio=f"As Oy {ext}",
                              analysis_status="done", first_seen_at=now, last_seen_at=now)
            listing.renovations.append(ListingRenovation(remonttityyppi="Putkiremontti", confidence=90, timing_year=year))
            db.session.add(listing)
            db.session.flush()
            svc.match_listing(listing)
        db.session.commit()
        page = targets.list_targets(org.id, filters={"sort": "year"})
        assert [m.listing.taloyhtio for m in page.items] == ["As Oy b", "As Oy a", "As Oy c"]
