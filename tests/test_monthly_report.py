"""Kuukausiraportti (app/analytics/monthly.py)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.analytics.monthly import Month, format_money, monthly_report, send_monthly_reports
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Activity, Lead
from app.listings.models import Listing, ListingMatch
from app.proposals.models import Proposal
from app.tasks.settings import get_organization_settings
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
OCT = datetime(2026, 10, 15, 10, tzinfo=timezone.utc)
SEP = datetime(2026, 9, 15, 10, tzinfo=timezone.utc)


def _org(slug):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    admin = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    admin.totp_enabled = True
    seller = create_user(f"user-{slug}@test.com", PASSWORD, role="user", organization_id=org.id)
    db.session.commit()
    return org, admin, seller


def _lead(org, ref, *, created, status="active", value=None, changed=None, owner=None):
    from app.api.services import upsert_lead

    lead, _ = upsert_lead(org.id, {"company": f"As Oy {ref}", "source": "import", "source_ref": f"import-{ref}"}, match_by_email=False)
    lead.created_at = created
    lead.status = status
    lead.deal_value = Decimal(value) if value is not None else None
    lead.stage_changed_at = changed
    lead.assigned_to = owner
    db.session.commit()
    return lead


def _match(org, ext, *, created, status="pending", lead=None, handled=None):
    listing = Listing(source="import", external_id=f"{org.id}-{ext}", maakunta="Uusimaa", taloyhtio=f"As Oy M{ext}")
    db.session.add(listing)
    db.session.flush()
    match = ListingMatch(
        organization_id=org.id,
        listing_id=listing.id,
        status=status,
        created_at=created,
        released_at=created,
        lead_id=lead.id if lead else None,
        lead_created_at=handled if status == "lead_created" else None,
        dismissed_at=handled if status == "dismissed" else None,
    )
    db.session.add(match)
    db.session.commit()


def _seed(org, seller):
    won = _lead(org, "won", created=OCT, status="won", value="12000", changed=OCT, owner=seller.id)
    _lead(org, "lost", created=OCT, status="lost", changed=OCT)
    _lead(org, "old", created=SEP, status="won", value="5000", changed=SEP)
    for i in range(4):
        _match(org, i, created=OCT)
    _match(org, 10, created=OCT, status="lead_created", lead=won, handled=OCT)
    _match(org, 11, created=OCT, status="dismissed", handled=OCT)
    _match(org, 12, created=SEP)
    for kind in ("call", "call", "email_sent"):
        db.session.add(Activity(lead_id=won.id, organization_id=org.id, user_id=seller.id, type=kind, created_at=OCT))
    db.session.add(
        Proposal(organization_id=org.id, lead_id=won.id, title="Putket", reference_number="FLW-1", status="accepted",
                 total=Decimal("15000"), sent_at=OCT, accepted_at=OCT)
    )
    db.session.commit()
    return won


def test_month_bounds_follow_finnish_time():
    start, end = Month(2026, 10).bounds()
    assert start == datetime(2026, 9, 30, 21, tzinfo=timezone.utc)  # 00:00 EEST
    assert end == datetime(2026, 10, 31, 22, tzinfo=timezone.utc)  # 00:00 EET
    assert Month(2026, 1).previous() == Month(2025, 12) and Month(2026, 12).next() == Month(2027, 1)
    assert Month.parse("2026-10").label == "Lokakuu 2026"
    assert Month.parse("bogus", today=date(2026, 11, 12)) == Month(2026, 11)
    assert format_money(Decimal("1234567.5")) == "1 234 567,50 €" and format_money(0) == "0,00 €"


def test_figures_compare_to_previous_month(app):
    with app.app_context():
        org, _admin, seller = _org("figures")
        other, _, other_seller = _org("figures-other")
        _seed(org, seller)
        _seed(other, other_seller)  # never counted for org
        report = monthly_report(org.id, Month(2026, 10))
        f = report["figures"]
        assert (f["received"], f["transferred"], f["hidden"]) == (6, 1, 1)
        assert (f["calls"], f["emails"]) == (2, 1)
        assert (f["proposals_sent"], f["proposals_accepted"]) == (1, 1)
        assert f["proposals_accepted_value"] == Decimal("15000.00")
        assert (f["won"], f["won_value"], f["lost"]) == (1, Decimal("12000.00"), 1)
        assert f["win_rate"] == 50 and f["transfer_rate"] == 17
        previous = report["previous_figures"]
        assert (previous["won"], previous["received"]) == (1, 1)
        won_row = next(r for r in report["rows"] if r["key"] == "won_value")
        assert won_row["change"] == Decimal("7000.00")
        assert report["people"] == [{"email": seller.email, "calls": 2, "emails": 1, "won": 1}]
        assert [lead.company for lead in report["won_deals"]] == ["As Oy won"]


def test_report_page(client, app):
    with app.app_context():
        org, admin, seller = _org("page")
        _seed(org, seller)
        email = seller.email
        admin_email = admin.email
        org_id = org.id
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    page = client.get("/reports/monthly?month=2026-10")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Lokakuu 2026" in html and "Syyskuu 2026" in html
    assert "12 000,00 €" in html and "Kauppoja voitettu" in html
    assert "Kytke pois" not in html  # only admins switch the email
    assert client.get("/reports/monthly?month=2999-01").status_code == 200  # future -> this month
    client.get("/auth/logout")
    client.post("/auth/login", data={"email": admin_email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    assert "Kuukausiraportti" in client.get("/reports").get_data(as_text=True)
    client.post("/reports/monthly/email", data={"enabled": "0", "month": "2026-10"})
    with app.app_context():
        assert get_organization_settings(org_id).monthly_report_enabled is False


def test_monthly_email_once_per_month_to_admins(app, monkeypatch):
    sent = []
    monkeypatch.setattr("app.email.services.send_system_message", lambda message: sent.append(message))
    with app.app_context():
        org, admin, seller = _org("mail")
        quiet, _, _ = _org("mailquiet")  # no activity: no email
        off, _, off_seller = _org("mailoff")
        _seed(org, seller)
        _seed(off, off_seller)
        get_organization_settings(off.id).monthly_report_enabled = False
        db.session.commit()

        now = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)  # 1.11. 08:30 Helsinki
        assert send_monthly_reports(now=now) == 1
        assert len(sent) == 1 and sent[0].recipients == [admin.email]
        assert "lokakuu 2026" in sent[0].subject and "12 000,00 €" in sent[0].html
        assert "/reports/monthly?month=2026-10" in sent[0].body
        assert send_monthly_reports(now=now) == 0  # once a month
        assert get_organization_settings(quiet.id).monthly_report_sent_for == "2026-10"


def test_two_listings_of_one_housing_company_are_one_target(app):
    with app.app_context():
        org, _admin, _seller = _org("samecompany")
        for ext, name in ((1, "As Oy Sama"), (2, "Asunto Oy Sama")):
            listing = Listing(source="import", external_id=f"s{ext}", maakunta="Uusimaa", kaupunki="Espoo", taloyhtio=name)
            db.session.add(listing)
            db.session.flush()
            db.session.add(ListingMatch(organization_id=org.id, listing_id=listing.id, status="pending", created_at=OCT, released_at=OCT))
        db.session.commit()
        assert monthly_report(org.id, Month(2026, 10))["figures"]["received"] == 1
