"""Features added after the 11/2026 audit: filters on Kohteet and leads, owner
and links on transfer, contact logging, lead restore, task editing, lead CSV
import, superadmin tools, proposal VAT breakdown, target digest."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService, get_default_stage
from app.listings import target_services as targets
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.notifications.models import Notification
from app.search.models import SearchProfile
from app.tasks.models import Task
from app.users.models import AuditLog, Organization, User
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"
PIPE = "Putkiremontti"
ROOF = "Katto- ja vesikattoremontti"
NOW = datetime.now(timezone.utc)


def _org(slug: str, role: str = "admin"):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    user = create_user(f"{role}-{slug}@test.com", PASSWORD, role=role, organization_id=org.id)
    user.totp_enabled = True
    db.session.commit()
    return org, user


def _superadmin(email: str) -> User:
    user = create_user(email, PASSWORD, role="superadmin")
    user.totp_enabled = True
    db.session.commit()
    return user


def _login(client, email: str):
    response = client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    return response


def _lead(org_id: int, **fields) -> Lead:
    lead = Lead(
        organization_id=org_id,
        stage_id=get_default_stage(org_id).id,
        status="active",
        source="manual",
        **fields,
    )
    db.session.add(lead)
    db.session.commit()
    return lead


def _profile(org) -> SearchProfile:
    profile = SearchProfile(
        organization_id=org.id,
        name="P",
        remonttityyppi=PIPE,
        regions=["Uusimaa"],
        source="oikotie",
        is_active=True,
        min_confidence=60,
        needs_rematch=False,
    )
    db.session.add(profile)
    db.session.flush()
    return profile


def _target(org, profile, ext, *, city="Espoo", rtype=PIPE, building="kerrostalo", email="isannointi@example.fi", created=None):
    listing = Listing(
        source="import",
        external_id=str(ext),
        maakunta="Uusimaa",
        kaupunki=city,
        osoite=f"Testitie {ext}",
        taloyhtio=f"As Oy Kohde {ext}",
        analysis_status="done",
        first_seen_at=NOW,
        last_seen_at=NOW,
        published_at=NOW - timedelta(minutes=int(ext)),
        building_type=building,
        isannoitsija="Isännöinti Oy" if email else None,
        isannoitsija_email=email,
    )
    db.session.add(listing)
    db.session.flush()
    db.session.add(ListingRenovation(listing_id=listing.id, remonttityyppi=rtype, confidence=90))
    match = ListingMatch(
        organization_id=org.id,
        profile_id=profile.id,
        listing_id=listing.id,
        status="pending",
        confidence=90,
        created_at=created or NOW,
    )
    db.session.add(match)
    db.session.commit()
    return match


# --- Kohteet: filters, sorting, owner, links ------------------------------------


def test_targets_can_be_filtered_and_sorted(app):
    with app.app_context():
        org, _ = _org("tfilter")
        profile = _profile(org)
        _target(org, profile, 1, city="Espoo")
        _target(org, profile, 2, city="Helsinki", rtype=ROOF, building="rivitalo")
        _target(org, profile, 3, city="Helsinki", email=None)

        def names(**filters):
            page = targets.list_targets(org.id, filters=filters)
            return [m.listing.external_id for m in page.items]

        assert names() == ["1", "2", "3"]
        assert names(city="helsinki") == ["2", "3"]
        assert names(renovation=ROOF) == ["2"]
        assert names(building="rivitalo") == ["2"]
        assert names(contact="1") == ["1", "2"]
        assert names(city="Helsinki", contact="1") == ["2"]
        assert names(sort="oldest") == ["3", "2", "1"]
        assert names(sort="city") == ["1", "2", "3"]
        assert names(city="Ei Olemassa") == []

        options = targets.target_filter_options(org.id)
        assert options["cities"] == ["Espoo", "Helsinki"]
        # only the organization's own urakat are offered (the profile wants PIPE;
        # ROOF is just another renovation on one of the listings)
        assert options["renovations"] == [PIPE]
        profile.remonttityypit = [PIPE, ROOF]
        db.session.commit()
        assert targets.target_filter_options(org.id)["renovations"] == sorted([PIPE, ROOF])
        assert options["buildings"] == ["kerrostalo", "rivitalo"]


def test_targets_page_keeps_filters_and_shows_the_bar(client, app):
    with app.app_context():
        org, user = _org("tpage")
        profile = _profile(org)
        _target(org, profile, 1, city="Espoo")
        _target(org, profile, 2, city="Helsinki")
        email = user.email
    _login(client, email)
    page = client.get("/targets?city=Helsinki").get_data(as_text=True)
    assert "As Oy Kohde 2" in page and "As Oy Kohde 1" not in page
    assert 'name="city"' in page and "Tyhjennä rajaukset" in page


def test_transfer_assigns_owner_and_links_the_new_lead(client, app):
    with app.app_context():
        org, admin = _org("towner")
        seller = create_user("seller-towner@test.com", PASSWORD, role="user", organization_id=org.id)
        profile = _profile(org)
        match_id = _target(org, profile, 1).id
        db.session.commit()
        email, seller_id, org_id = admin.email, seller.id, org.id
    _login(client, email)
    response = client.post(
        "/targets/transfer", data={"single_id": match_id, "assigned_to": seller_id}, follow_redirects=True
    )
    page = response.get_data(as_text=True)
    with app.app_context():
        lead = Lead.query.filter_by(organization_id=org_id).one()
        assert lead.assigned_to == seller_id
        assert f"/leads/{lead.id}" in page and "Liidi luotu:" in page


# --- leads: quick filters, contact log, restore ---------------------------------


def test_leads_quick_filters_by_target_details(client, app):
    with app.app_context():
        org, user = _org("lfilter")
        _lead(org.id, company="As Oy Espoo", ai_contact_info={"kaupunki": "Espoo", "remonttityyppi": PIPE})
        _lead(org.id, company="As Oy Tampere", ai_contact_info={"kaupunki": "Tampere", "remonttityyppi": ROOF})
        _lead(org.id, company="Käsin Lisätty Oy")
        options = LeadService.quick_filter_options(org.id)
        assert options["city"] == ["Espoo", "Tampere"]
        email = user.email
    _login(client, email)
    page = client.get("/leads?city=tampere").get_data(as_text=True)
    assert "As Oy Tampere" in page and "As Oy Espoo" not in page and "Käsin Lisätty" not in page
    page = client.get(f"/leads?renovation={PIPE}").get_data(as_text=True)
    assert "As Oy Espoo" in page and "As Oy Tampere" not in page


def test_logging_a_call_marks_the_lead_contacted(client, app):
    with app.app_context():
        org, user = _org("logcall")
        lead_id = _lead(org.id, company="As Oy Soitto").id
        email = user.email
    _login(client, email)
    response = client.post(f"/leads/{lead_id}/log-contact", data={"kind": "call", "note": "Ei vastannut"})
    assert response.status_code == 302
    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert lead.last_contacted_at is not None
        activity = Activity.query.filter_by(lead_id=lead_id, type="call").one()
        assert activity.content == "Ei vastannut"
    client.post(f"/leads/{lead_id}/log-contact", data={"kind": "email"})
    with app.app_context():
        assert Activity.query.filter_by(lead_id=lead_id, type="email_sent").count() == 1


def test_archived_and_lost_leads_can_be_reopened(client, app):
    with app.app_context():
        org, user = _org("restore")
        archived = _lead(org.id, company="As Oy Arkisto")
        lost = _lead(org.id, company="As Oy Hävitty")
        LeadService.archive(archived.id, org.id, user.id)
        from app.leads.models import PipelineStage

        lost_stage = PipelineStage.query.filter_by(organization_id=org.id, name="Hävitty").one()
        LeadService.move_stage(lost.id, lost_stage.id, org.id, user.id, lost_reason="no_budget")
        db.session.commit()
        email, archived_id, lost_id = user.email, archived.id, lost.id
    _login(client, email)
    assert "Palauta arkistosta" in client.get(f"/leads/{archived_id}").get_data(as_text=True)
    client.post(f"/leads/{archived_id}/restore")
    client.post(f"/leads/{lost_id}/restore")
    with app.app_context():
        assert db.session.get(Lead, archived_id).status == "active"
        reopened = db.session.get(Lead, lost_id)
        assert (reopened.status, reopened.lost_reason) == ("active", None)
        assert reopened.stage.name == "Uusi liidi"


# --- tasks ----------------------------------------------------------------------


def _task(org_id: int, user_id: int, **fields) -> Task:
    task = Task(
        organization_id=org_id,
        assigned_to=user_id,
        created_by=user_id,
        title="Soita",
        due_date=NOW + timedelta(days=1),
        **fields,
    )
    db.session.add(task)
    db.session.commit()
    return task


def test_task_can_be_edited_rescheduled_and_deleted(client, app):
    with app.app_context():
        org, user = _org("taskedit")
        task_id = _task(org.id, user.id).id
        email = user.email
    _login(client, email)
    assert client.get(f"/tasks/{task_id}/edit").status_code == 200
    response = client.post(
        f"/tasks/{task_id}/edit",
        data={"title": "Lähetä tarjous", "due_date": "2027-01-15T09:30", "priority": "high", "type": "email"},
    )
    assert response.status_code == 302
    with app.app_context():
        task = db.session.get(Task, task_id)
        assert (task.title, task.priority, task.type) == ("Lähetä tarjous", "high", "email")
        assert (task.due_date.year, task.due_date.month, task.due_date.hour) == (2027, 1, 9)
    assert client.post(f"/tasks/{task_id}/delete").status_code == 302
    with app.app_context():
        assert db.session.get(Task, task_id) is None


def test_salesperson_cannot_change_a_colleagues_task(client, app):
    with app.app_context():
        org, admin = _org("taskperm")
        seller = create_user("seller-taskperm@test.com", PASSWORD, role="user", organization_id=org.id)
        db.session.commit()
        task_id = _task(org.id, admin.id).id
        seller_email = seller.email
    _login(client, seller_email)
    assert client.post(f"/tasks/{task_id}/delete").status_code == 403
    assert client.post(f"/tasks/{task_id}/edit", data={"title": "x", "due_date": "2027-01-15T09:30"}).status_code == 403
    with app.app_context():
        assert db.session.get(Task, task_id).title == "Soita"


def test_task_of_another_organization_is_not_found(client, app):
    with app.app_context():
        org, user = _org("taskiso")
        other, other_user = _org("taskiso2")
        task_id = _task(other.id, other_user.id).id
        email = user.email
    _login(client, email)
    assert client.get(f"/tasks/{task_id}/edit").status_code == 404
    assert client.post(f"/tasks/{task_id}/delete").status_code == 404


# --- lead CSV import --------------------------------------------------------------


def test_customer_imports_leads_from_csv(client, app):
    with app.app_context():
        org, user = _org("csvimport")
        _lead(org.id, company="As Oy Vanha", email="vanha@example.fi")
        email, org_id = user.email, org.id
    _login(client, email)
    csv_text = (
        "Yritys;Nimi;Sähköposti;Puhelin;Kaupunki;Kaupan arvo;Tunnisteet\n"
        "As Oy Äänekoski;Matti Meikäläinen;matti@example.fi;040 123;Äänekoski;45 000,50;messut, putki\n"
        "As Oy Vanha;;VANHA@example.fi;;;;\n"  # duplicate of an existing lead
        "As Oy Äänekoski;;matti@example.fi;;;;\n"  # duplicate inside the file
        "As Oy Huono;;ei-sahkoposti;;;;\n"  # rejected by the lead rules
        ";;;;;;\n"
    )
    response = client.post(
        "/leads/import",
        data={"file": (io.BytesIO(csv_text.encode("cp1252")), "liidit.csv")},
        content_type="multipart/form-data",
    )
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "<strong>1</strong> liidi luotu" in page and "rivi 5" in page
    with app.app_context():
        lead = Lead.query.filter_by(organization_id=org_id, company="As Oy Äänekoski").one()
        assert (lead.first_name, lead.last_name, lead.source) == ("Matti", "Meikäläinen", "import")
        assert lead.deal_value == Decimal("45000.50")
        assert lead.ai_contact_info["kaupunki"] == "Äänekoski"
        assert Lead.query.filter_by(organization_id=org_id).count() == 2

    template = client.get("/leads/import-template.csv").get_data(as_text=True)
    assert template.startswith("﻿yritys;etunimi;")


def test_import_rejects_a_file_without_known_columns(client, app):
    with app.app_context():
        _, user = _org("csvbad")
        email = user.email
    _login(client, email)
    response = client.post(
        "/leads/import",
        data={"file": (io.BytesIO(b"foo;bar\n1;2\n"), "x.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "tunnistettavia sarakkeita" in response.get_data(as_text=True)


# --- superadmin tools -----------------------------------------------------------------


def test_deactivated_organization_cannot_sign_in_or_use_api(client, app):
    from app.api.services import create_api_key

    with app.app_context():
        org, user = _org("orgoff")
        _superadmin("sa-orgoff@test.com")
        _key, raw_key = create_api_key(org.id, "test", created_by=user.id)
        db.session.commit()
        email, org_id = user.email, org.id

    _login(client, "sa-orgoff@test.com")
    assert client.post(f"/admin/customers/{org_id}/active", data={"active": "0"}).status_code == 302
    client.get("/auth/logout")

    response = client.post("/auth/login", data={"email": email, "password": PASSWORD}, follow_redirects=True)
    assert "ei ole käytössä" in response.get_data(as_text=True)
    assert client.get("/leads").status_code == 302  # not signed in
    assert client.get("/api/v1/leads", headers={"X-API-Key": raw_key}).status_code in (401, 403)

    with app.app_context():
        assert AuditLog.query.filter_by(action="organization_deactivated", organization_id=org_id).count() == 1
        db.session.get(Organization, org_id).is_active = True
        db.session.commit()
    assert client.post("/auth/login", data={"email": email, "password": PASSWORD}).status_code == 302
    assert client.get("/api/v1/leads", headers={"X-API-Key": raw_key}).status_code == 200


def test_superadmin_resets_a_users_two_factor(client, app):
    from app.auth.models import BackupCode

    with app.app_context():
        org, user = _org("reset2fa")
        user.totp_secret = "JBSWY3DPEHPK3PXP"
        db.session.add(BackupCode(user_id=user.id, code_hash="x" * 64))
        _superadmin("sa-reset2fa@test.com")
        db.session.commit()
        user_id, admin_email = user.id, user.email

    # an organization admin may not do it
    _login(client, admin_email)
    assert client.post(f"/admin/users/{user_id}/reset-2fa").status_code == 403
    client.get("/auth/logout")

    _login(client, "sa-reset2fa@test.com")
    assert client.post(f"/admin/users/{user_id}/reset-2fa").status_code == 302
    with app.app_context():
        user = db.session.get(User, user_id)
        assert (user.totp_enabled, user.totp_secret) == (False, None)
        assert BackupCode.query.filter_by(user_id=user_id).count() == 0
        assert AuditLog.query.filter_by(action="two_factor_reset").count() == 1


def test_audit_log_page_is_for_superadmins_and_filters(client, app):
    with app.app_context():
        org, user = _org("auditpage")
        _superadmin("sa-auditpage@test.com")
        db.session.add(AuditLog(action="leads_imported", organization_id=org.id, user_id=user.id))
        db.session.add(AuditLog(action="lead_archived", organization_id=org.id, user_id=user.id))
        db.session.commit()
        admin_email, org_id = user.email, org.id
    _login(client, admin_email)
    assert client.get("/admin/audit-log").status_code == 403
    client.get("/auth/logout")
    _login(client, "sa-auditpage@test.com")
    page = client.get(f"/admin/audit-log?org={org_id}&action=leads_imported").get_data(as_text=True)
    assert "Liidejä tuotu tiedostosta" in page and "<td>Liidi arkistoitu</td>" not in page


# --- proposals -----------------------------------------------------------------------


def test_proposal_pdf_shows_vat_breakdown_and_logo(app):
    from app.proposals.pdf import ProposalPDFService, fi_money, fi_number
    from app.proposals.services import ProposalService

    assert fi_money("12500.5") == "12 500,50 €"
    assert fi_money(0) == "0,00 €"
    assert (fi_number("1.0000"), fi_number("25.50")) == ("1", "25,5")

    with app.app_context():
        org, user = _org("pdfvat")
        lead = _lead(org.id, company="As Oy PDF", email="pj@example.fi")
        proposal = ProposalService.create(
            lead.id,
            {
                "title": "Putkiremontti",
                "tax_percent": "25.5",
                "line_items": [{"description": "Työ", "quantity": 1, "unit_price": 1000}],
            },
            user.id,
            org.id,
        )
        db.session.commit()
        html = ProposalPDFService.render_html(proposal, logo_url="https://example.fi/logo.png")
        assert "Veroton yhteensä" in html and "1 000,00 €" in html
        assert "ALV 25,5 %" in html and "255,00 €" in html
        assert "Yhteensä (sis. ALV)" in html and "1 255,00 €" in html
        assert '<img class="logo" src="https://example.fi/logo.png"' in html
        # only https logos are rendered
        assert "<img" not in ProposalPDFService.render_html(proposal, logo_url="file:///etc/passwd")


# --- target digest -----------------------------------------------------------------------


def test_target_digest_notifies_once_about_new_targets(app):
    from app.tasks.settings import get_organization_settings

    with app.app_context():
        org, admin = _org("digest")
        seller = create_user("seller-digest@test.com", PASSWORD, role="user", organization_id=org.id)
        quiet, _ = _org("digestquiet")
        off, _ = _org("digestoff")
        profile = _profile(org)
        _target(org, profile, 1)
        _target(org, profile, 2)
        off_profile = _profile(off)
        _target(off, off_profile, 3)
        get_organization_settings(off.id).targets_digest_enabled = False
        db.session.commit()

        assert targets.send_target_digests(now=NOW + timedelta(minutes=5)) == 1
        notes = Notification.query.filter_by(organization_id=org.id).all()
        assert {n.user_id for n in notes} == {admin.id, seller.id}
        assert notes[0].title == "2 uutta kohdetta" and notes[0].link == "/targets"
        assert Notification.query.filter(Notification.organization_id.in_([quiet.id, off.id])).count() == 0

        # nothing new since -> no second notification
        assert targets.send_target_digests(now=NOW + timedelta(hours=24)) == 0
        _target(org, profile, 4, created=NOW + timedelta(hours=30))
        assert targets.send_target_digests(now=NOW + timedelta(hours=48)) == 1
        assert Notification.query.filter_by(user_id=admin.id, title="1 uusi kohde").count() == 1


def test_digest_can_be_switched_off_from_the_targets_page(client, app):
    from app.tasks.settings import get_organization_settings

    with app.app_context():
        org, user = _org("digesttoggle")
        email, org_id = user.email, org.id
    _login(client, email)
    assert client.post("/targets/digest", data={"enabled": "0"}).status_code == 302
    with app.app_context():
        assert get_organization_settings(org_id).targets_digest_enabled is False


# --- no third-party CDN --------------------------------------------------------------------


def test_pages_load_libraries_from_our_own_origin(client, app):
    with app.app_context():
        _, user = _org("vendor")
        email = user.email
    login_page = client.get("/auth/login").get_data(as_text=True)
    _login(client, email)
    dashboard = client.get("/dashboard").get_data(as_text=True)
    for page in (login_page, dashboard):
        for host in ("googleapis.com", "gstatic.com", "jsdelivr.net", "cdnjs.cloudflare.com"):
            assert host not in page
    assert "/static/vendor/chart-4.4.1.umd.js" in dashboard
    assert client.get("/static/vendor/inter.css").status_code == 200
    assert client.get("/static/vendor/fonts/inter-latin-400-normal.woff2").status_code == 200
