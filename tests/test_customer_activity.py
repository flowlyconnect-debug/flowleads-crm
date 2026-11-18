"""Asiakasseuranta (app/admin/activity_services.py, app/core/usage.py): what
each customer does with the targets, and the signals of a customer who
collects contacts to resell them."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.admin.activity_services import customer_activity, customer_activity_detail
from app.admin.models import UsageDaily
from app.core.audit import log_audit
from app.core.permissions import TWO_FA_SESSION_KEY
from app.core.usage import track
from app.extensions import db
from app.leads.models import Activity, Lead, PipelineStage
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
NOW = datetime.now(timezone.utc)


def _org(slug):
    org = create_organization(f"Asiakas {slug}", slug)
    db.session.flush()
    user = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    user.totp_enabled = True
    db.session.commit()
    return org, user


def _stage(org_id):
    return PipelineStage.query.filter_by(organization_id=org_id).order_by(PipelineStage.order_index).first().id


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def _row(org_id, rows):
    return next(r for r in rows if r["org"].id == org_id)


def test_track_adds_to_one_row_per_day(app):
    with app.app_context():
        org, user = _org("laskuri")
        track("company_card_view", organization_id=org.id, user_id=user.id)
        track("company_card_view", 4, organization_id=org.id, user_id=user.id)
        track("leads_exported", 30, organization_id=org.id, user_id=user.id)
        track("unknown_metric", organization_id=org.id, user_id=user.id)  # ignored
        rows = {r.metric: r.count for r in UsageDaily.query.filter_by(organization_id=org.id)}
        assert rows == {"company_card_view": 5, "leads_exported": 30}


def test_a_normal_customer_has_no_signals(app):
    with app.app_context():
        org, user = _org("normaali")
        track("targets_view", 40, organization_id=org.id, user_id=user.id)
        track("targets_transferred", 10, organization_id=org.id, user_id=user.id)
        row = _row(org.id, customer_activity())
        assert row["level"] == "ok" and row["signals"] == []
        assert row["views"] == 40 and row["transferred"] == 10


def test_exporting_and_not_working_the_leads_is_flagged(app):
    with app.app_context():
        org, user = _org("myyja")
        track("targets_transferred", 60, organization_id=org.id, user_id=user.id)
        track("leads_exported", 60, organization_id=org.id, user_id=user.id)
        old = NOW - timedelta(days=10)
        for n in range(25):
            lead = Lead(organization_id=org.id, first_name="L", last_name=str(n), company=f"As Oy {n}",
                        source="oikotie", source_ref=f"r{n}", created_at=old, stage_id=_stage(org.id))
            db.session.add(lead)
        db.session.flush()
        # one of them is worked: a call
        first = Lead.query.filter_by(organization_id=org.id).first()
        db.session.add(Activity(lead_id=first.id, organization_id=org.id, user_id=user.id, type="call", content="Soitto"))
        db.session.commit()

        row = _row(org.id, customer_activity())
        keys = {s.key for s in row["signals"]}
        assert {"export", "unworked"} <= keys
        assert row["level"] == "check" and row["unworked"] == 24 and row["old_leads"] == 25
        # they come first on the list
        assert customer_activity()[0]["org"].id == org.id


def test_heavy_browsing_and_many_addresses_are_flagged(app):
    with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.1"}):
        org, user = _org("selaaja")
        track("company_card_view", 350, organization_id=org.id, user_id=user.id)
        for n in range(7):
            entry = log_audit("login_success", user_id=user.id, organization_id=org.id)
            entry.ip_address = f"192.0.2.{n}"
        db.session.commit()
        row = _row(org.id, customer_activity())
        keys = {s.key for s in row["signals"]}
        assert "views_day" in keys and "ips" in keys


def test_superadmins_are_not_counted(app, client):
    with app.app_context():
        org, _ = _org("katsottu")
        admin = create_user("root-seuranta@test.com", PASSWORD, role="superadmin")
        admin.totp_enabled = True
        db.session.commit()
        org_id = org.id
    _login(client, "root-seuranta@test.com")
    client.get(f"/targets?organization_id={org_id}")
    with app.app_context():
        assert UsageDaily.query.filter_by(organization_id=org_id).count() == 0


def test_customer_pages_and_exports_are_counted(app, client):
    with app.app_context():
        org, user = _org("kaytto")
        lead = Lead(organization_id=org.id, first_name="A", last_name="B", company="As Oy Vienti", source="manual",
                    stage_id=_stage(org.id))
        db.session.add(lead)
        db.session.commit()
        org_id, email, lead_id = org.id, user.email, lead.id
    _login(client, email)
    assert client.get("/targets").status_code == 200
    assert client.get(f"/leads/{lead_id}").status_code == 200
    assert client.get("/leads/export").status_code == 200
    with app.app_context():
        rows = {r.metric: r.count for r in UsageDaily.query.filter_by(organization_id=org_id)}
        assert rows["targets_view"] == 1 and rows["lead_view"] == 1 and rows["leads_exported"] == 1
        detail = customer_activity_detail(org_id)
        assert any(e.action == "leads_exported" for e in detail["events"])
        assert detail["users"][0]["views"] == 2


def test_activity_pages_are_superadmin_only(app, client):
    with app.app_context():
        org, user = _org("sivu")
        track("targets_view", 3, organization_id=org.id, user_id=user.id)
        admin = create_user("root-sivu@test.com", PASSWORD, role="superadmin")
        admin.totp_enabled = True
        db.session.commit()
        org_id, email = org.id, user.email
    _login(client, email)
    assert client.get("/admin/activity").status_code in (302, 403)
    client.get("/auth/logout")
    _login(client, "root-sivu@test.com")
    page = client.get("/admin/activity?days=7").get_data(as_text=True)
    assert "Asiakasseuranta" in page and "Asiakas sivu" in page
    detail = client.get(f"/admin/activity/{org_id}").get_data(as_text=True)
    assert "Kohteet-sivu" in detail and "admin-sivu@test.com" in detail
    assert client.get("/admin/activity/999999").status_code == 404
