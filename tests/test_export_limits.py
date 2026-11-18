"""Lead exports: admins only and a daily row limit per customer
(app/core/export_limits.py)."""

from __future__ import annotations

from app.core.permissions import TWO_FA_SESSION_KEY
from app.core.usage import track
from app.extensions import db
from app.leads.models import Lead, PipelineStage
from app.users.models import AuditLog, Organization
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"


def _org(slug, leads=3):
    org = create_organization(f"Asiakas {slug}", slug)
    db.session.flush()
    admin = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    seller = create_user(f"myyja-{slug}@test.com", PASSWORD, role="user", organization_id=org.id)
    admin.totp_enabled = seller.totp_enabled = True
    stage = PipelineStage.query.filter_by(organization_id=org.id).order_by(PipelineStage.order_index).first()
    for n in range(leads):
        db.session.add(Lead(organization_id=org.id, first_name="L", last_name=str(n), company=f"As Oy {n}",
                            source="manual", stage_id=stage.id, assigned_to=seller.id))
    db.session.commit()
    return org


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def _is_csv(response):
    return response.status_code == 200 and response.mimetype == "text/csv"


def test_admin_exports_within_the_limit(app, client):
    with app.app_context():
        org = _org("raja1")
        org_id = org.id
    _login(client, "admin-raja1@test.com")
    assert _is_csv(client.get("/leads/export"))


def test_a_seller_cannot_export(app, client):
    with app.app_context():
        org = _org("raja2")
        org_id = org.id
    _login(client, "myyja-raja2@test.com")
    response = client.get("/leads/export", follow_redirects=True)
    assert response.mimetype == "text/html" and "vain pääkäyttäjille" in response.get_data(as_text=True)
    with app.app_context():
        assert AuditLog.query.filter_by(organization_id=org_id, action="export_blocked").count() == 1


def test_the_daily_limit_refuses_the_whole_export(app, client):
    with app.app_context():
        org = _org("raja3", leads=5)
        org_id = org.id
        app.config["EXPORT_DAILY_ROWS"] = 6
        track("leads_exported", 3, organization_id=org_id)
    try:
        _login(client, "admin-raja3@test.com")
        response = client.get("/leads/export", follow_redirects=True)
        html = response.get_data(as_text=True)
        assert response.mimetype == "text/html"
        assert "Päivän vientiraja on 6 liidiä. Tänään on jo viety 3, joten jäljellä on 3." in html
    finally:
        app.config["EXPORT_DAILY_ROWS"] = 200


def test_zero_means_no_limit(app, client):
    with app.app_context():
        org = _org("raja7", leads=5)
        org.export_daily_limit = 0  # a customer's own limit overrides the default
        db.session.commit()
        app.config["EXPORT_DAILY_ROWS"] = 2
    try:
        _login(client, "admin-raja7@test.com")
        assert _is_csv(client.get("/leads/export"))
    finally:
        app.config["EXPORT_DAILY_ROWS"] = 200


def test_the_limit_counts_crm_files_too(app, client):
    with app.app_context():
        org = _org("raja4", leads=4)
        org_id = org.id
        org.export_daily_limit = 5
        db.session.commit()
    _login(client, "admin-raja4@test.com")
    assert _is_csv(client.get("/leads/export/hubspot.csv"))  # 4 rows
    response = client.get("/leads/export", follow_redirects=True)  # 4 more: over 5
    assert response.mimetype == "text/html" and "jäljellä on 1" in response.get_data(as_text=True)


def test_switch_turns_the_limits_off(app, client):
    with app.app_context():
        _org("raja5")
    app.config["EXPORT_LIMITS_ENABLED"] = False
    try:
        _login(client, "myyja-raja5@test.com")
        assert _is_csv(client.get("/leads/export"))
    finally:
        app.config["EXPORT_LIMITS_ENABLED"] = True


def test_superadmin_sets_the_limit(app, client):
    with app.app_context():
        org = _org("raja6")
        org_id = org.id
        root = create_user("root-raja@test.com", PASSWORD, role="superadmin")
        root.totp_enabled = True
        db.session.commit()
    _login(client, "root-raja@test.com")
    response = client.post(f"/admin/customers/{org_id}/export-limit", data={"export_limit": "500"}, follow_redirects=True)
    assert "vientiraja 500 riviä päivässä" in response.get_data(as_text=True)
    client.post(f"/admin/customers/{org_id}/export-limit", data={"export_limit": ""})
    with app.app_context():
        assert db.session.get(Organization, org_id).export_daily_limit is None


def test_sellers_do_not_see_export_buttons(app, client):
    with app.app_context():
        _org("napit")
    _login(client, "myyja-napit@test.com")
    page = client.get("/leads/").get_data(as_text=True)
    assert "HubSpot-tuontitiedosto" not in page and 'value="export"' not in page
    client.get("/auth/logout")
    _login(client, "admin-napit@test.com")
    page = client.get("/leads/").get_data(as_text=True)
    assert "HubSpot-tuontitiedosto" in page and 'value="export"' in page


def test_limit_fields_accept_any_number(app, client):
    """step="50" made the browser refuse e.g. 120 without a word (found by hand)."""
    with app.app_context():
        _org("kentta")
        root = create_user("root-kentta@test.com", PASSWORD, role="superadmin")
        root.totp_enabled = True
        db.session.commit()
    _login(client, "root-kentta@test.com")
    page = client.get("/admin/customers").get_data(as_text=True)
    assert 'step="1" name="export_limit"' in page and 'step="1" name="limit"' in page
