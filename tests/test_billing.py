"""Public price list, free trial and the monthly contract (app/billing)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.billing import services as billing
from app.billing.plans import PLANS, TRIAL_DAYS
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.notifications.models import Notification
from app.users.models import Organization
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
NOW = datetime(2026, 11, 12, 10, tzinfo=timezone.utc)


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr("app.email.services.send_system_message", lambda message: sent.append(message) or True)
    return sent


def _org(slug, *, status="active"):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    admin = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    admin.totp_enabled = True
    seller = create_user(f"user-{slug}@test.com", PASSWORD, role="user", organization_id=org.id)
    if status == "trial":
        billing.start_trial(org, now=NOW)
    db.session.commit()
    return org, admin, seller


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def test_states_follow_dates(app):
    with app.app_context():
        org, admin, _ = _org("states", status="trial")
        state = billing.subscription_state(org, now=NOW + timedelta(days=3))
        assert (state.status, state.can_use, state.days_left) == ("trial", True, TRIAL_DAYS - 3)
        assert state.last_day == "26.11.2026"  # ends 26.11. at 12.00 Finnish time
        assert billing.subscription_state(org, now=NOW + timedelta(days=TRIAL_DAYS, seconds=1)).status == "trial_expired"
        assert not billing.subscription_state(org, now=NOW + timedelta(days=TRIAL_DAYS + 1)).can_use
        # organizations from before plans existed are active
        old = create_organization("Vanha", "vanha")
        db.session.commit()
        assert billing.subscription_state(old).status == "active"


def test_subscribe_cancel_resume(app, mails):
    with app.app_context():
        superadmin = create_user("super-billing@test.com", PASSWORD, role="superadmin")
        db.session.commit()
        org, admin, _ = _org("contract", status="trial")
        billing.subscribe(org, "flowleads", actor=admin, now=NOW + timedelta(days=20))
        assert (org.subscription_status, org.plan) == ("active", "flowleads")
        assert mails[-1].recipients == [superadmin.email] and "FlowLeads 149" in mails[-1].subject
        with pytest.raises(billing.BillingError):
            billing.subscribe(org, "flowleads", actor=admin)
        with pytest.raises(billing.BillingError):
            billing.subscribe(org, "yritys", actor=admin)  # no such plan

        ends = billing.cancel(org, actor=admin, now=datetime(2026, 12, 10, 12, tzinfo=timezone.utc))
        assert ends == datetime(2026, 12, 31, 22, tzinfo=timezone.utc)  # 1.1. 00:00 Helsinki
        state = billing.subscription_state(org, now=datetime(2026, 12, 20, tzinfo=timezone.utc))
        assert (state.status, state.can_use, state.last_day) == ("cancelled", True, "31.12.2026")
        assert billing.subscription_state(org, now=datetime(2027, 1, 1, tzinfo=timezone.utc)).status == "expired"
        assert "Älä laskuta" in mails[-1].body
        billing.resume(org, actor=admin, now=datetime(2026, 12, 20, tzinfo=timezone.utc))
        assert org.subscription_status == "active" and org.cancel_at is None


def test_onboarding_and_invite_start_trials(app, mails):
    from app.admin.invite_services import accept_invite, create_invite
    from app.admin.onboarding_services import create_customer

    with app.app_context():
        superadmin = create_user("super-onb@test.com", PASSWORD, role="superadmin")
        db.session.commit()
        result = create_customer(
            actor=superadmin, organization_name="Kokeilu Oy", admin_email="k@test.com", admin_password=None,
            remonttityypit=["Putkiremontti"], trial=True,
        )
        assert result.organization.subscription_status == "trial" and result.organization.trial_ends_at
        paying = create_customer(
            actor=superadmin, organization_name="Maksava Oy", admin_email="m@test.com", admin_password=None,
            remonttityypit=["Putkiremontti"],
        )
        assert paying.organization.subscription_status == "active"

        invite = create_invite(
            actor=superadmin, organization_name="Kutsuttu Oy", admin_email="kutsu@test.com",
            remonttityypit=["Putkiremontti"], trial=True,
        )
        db.session.commit()
        assert invite.organization.subscription_status == "trial" and invite.organization.trial_ends_at is None
        accept_invite(invite.invite, password="securepassword1")
        db.session.commit()
        org = db.session.get(Organization, invite.organization.id)
        assert org.trial_ends_at is not None and billing.subscription_state(org).status == "trial"


def test_ended_trial_keeps_data_but_blocks_new_targets(client, app):
    from app.listings.models import Listing, ListingMatch

    with app.app_context():
        org, admin, seller = _org("ended", status="trial")
        org.trial_ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        listing = Listing(source="import", external_id="e1", maakunta="Uusimaa", taloyhtio="As Oy Loppu", analysis_status="done")
        db.session.add(listing)
        db.session.flush()
        match = ListingMatch(organization_id=org.id, listing_id=listing.id, status="pending", released_at=datetime.now(timezone.utc) - timedelta(days=1))
        db.session.add(match)
        db.session.commit()
        match_id, email = match.id, seller.email
    _login(client, email)
    page = client.get("/targets").get_data(as_text=True)
    assert "As Oy Loppu" in page  # still visible
    assert "Kokeilujakso päättynyt" in page and "Valitse kuukausisopimus" in page
    response = client.post("/targets/transfer", data={"single_id": str(match_id)}, follow_redirects=True)
    assert "Kokeilujakso tai sopimus on päättynyt" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(ListingMatch, match_id).status == "pending"
    app.config["TARGET_FETCH_ENABLED"] = True
    try:
        fetch = client.post("/targets/fetch", headers={"Accept": "application/json"})
        assert fetch.status_code == 402 and fetch.get_json()["code"] == "subscription"
    finally:
        app.config["TARGET_FETCH_ENABLED"] = False
    # leads can still be exported
    assert client.get("/leads/export").status_code == 200


def test_subscription_pages(client, app, mails):
    with app.app_context():
        org, admin, seller = _org("pages", status="trial")
        admin_email, seller_email, org_id = admin.email, seller.email, org.id
    _login(client, seller_email)
    page = client.get("/billing").get_data(as_text=True)
    assert "Kokeilujakso" in page and "Tilausta hallinnoi organisaatiosi ylläpitäjä" in page
    assert client.post("/billing/subscribe", data={"plan": "flowleads", "accept_terms": "1"}).status_code in (302, 403)
    assert "päivää jäljellä" in client.get("/dashboard").get_data(as_text=True)
    client.get("/auth/logout")

    _login(client, admin_email)
    no_terms = client.post("/billing/subscribe", data={"plan": "flowleads"}, follow_redirects=True).get_data(as_text=True)
    assert "Hyväksy sopimusehdot" in no_terms
    ordered = client.post("/billing/subscribe", data={"plan": "flowleads", "accept_terms": "1"}, follow_redirects=True)
    assert "Kuukausisopimus (FlowLeads) on voimassa" in ordered.get_data(as_text=True)
    cancelled = client.post("/billing/cancel", follow_redirects=True).get_data(as_text=True)
    assert "Sopimus irtisanottu" in cancelled and "eikä seuraavaa kuukautta laskuteta" in cancelled
    resumed = client.post("/billing/resume", follow_redirects=True).get_data(as_text=True)
    assert "Irtisanominen peruttu" in resumed
    with app.app_context():
        org = db.session.get(Organization, org_id)
        assert (org.subscription_status, org.plan) == ("active", "flowleads")


def test_public_price_list_and_trial_request(client, app, mails):
    with app.app_context():
        create_user("super-price@test.com", PASSWORD, role="superadmin")
        db.session.commit()
    page = client.get("/hinnasto")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    for plan in PLANS:
        assert plan["name"] in html
    assert "149 €" in html and "290 €" not in html and "Rajaton määrä käyttäjiä" in html
    assert "irtisanottavissa milloin tahansa" in html and f"{TRIAL_DAYS} päivän maksuton kokeilu" in html
    assert "cdn" not in html.lower()

    bad = client.post("/hinnasto/kokeilu", data={"name": "Matti", "company": "Urakka Oy", "email": "ei-osoite"}, follow_redirects=True)
    assert "Anna kelvollinen sähköpostiosoite" in bad.get_data(as_text=True)
    ok = client.post(
        "/hinnasto/kokeilu",
        data={"name": "Matti", "company": "Urakka Oy", "email": "matti@urakka.fi", "plan": "flowleads", "message": "putket"},
        follow_redirects=True,
    )
    assert "Kiitos! Otamme yhteyttä" in ok.get_data(as_text=True)
    assert mails[-1].recipients == ["super-price@test.com"] and mails[-1].reply_to == "matti@urakka.fi"
    assert "Urakka Oy" in mails[-1].subject
    # a bot filling the hidden field gets the thank-you page and no email
    count = len(mails)
    client.post("/hinnasto/kokeilu", data={"name": "x", "company": "y", "email": "a@b.fi", "website": "spam"})
    assert len(mails) == count
    assert "Hinnasto" in client.get("/auth/login").get_data(as_text=True)


def test_trial_reminder_once(app, mails):
    with app.app_context():
        org, admin, seller = _org("remind", status="trial")
        assert billing.send_trial_reminders(now=NOW + timedelta(days=5)) == 0  # too early
        assert billing.send_trial_reminders(now=NOW + timedelta(days=TRIAL_DAYS - 2)) == 1
        assert billing.send_trial_reminders(now=NOW + timedelta(days=TRIAL_DAYS - 1)) == 0
        notes = Notification.query.filter_by(organization_id=org.id, type="trial_ending").all()
        assert [n.user_id for n in notes] == [admin.id]
        assert mails[-1].recipients == [admin.email]


def test_admin_controls(client, app, mails):
    with app.app_context():
        superadmin = create_user("super-ctl@test.com", PASSWORD, role="superadmin")
        superadmin.totp_enabled = True
        org, _, _ = _org("ctl", status="trial")
        db.session.commit()
        org_id = org.id
    _login(client, "super-ctl@test.com")
    page = client.get("/admin/customers").get_data(as_text=True)
    assert "Kokeilujakso" in page and "Sopimus voimaan" in page
    client.post(f"/admin/customers/{org_id}/subscription", data={"action": "extend_trial"})
    with app.app_context():
        ends = db.session.get(Organization, org_id).trial_ends_at.replace(tzinfo=timezone.utc)
        assert ends > NOW + timedelta(days=TRIAL_DAYS)
    client.post(f"/admin/customers/{org_id}/subscription", data={"action": "activate", "plan": "flowleads"})
    with app.app_context():
        assert db.session.get(Organization, org_id).subscription_status == "active"
    client.post(f"/admin/customers/{org_id}/subscription", data={"action": "end"})
    with app.app_context():
        assert billing.subscription_state(db.session.get(Organization, org_id)).status == "expired"
