"""Optional modules (sequences, automations, AI) are off until a superadmin
switches them on for a customer: nothing of them shows in the customer's view."""

from __future__ import annotations

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.services import LeadService, get_default_stage
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"


def _org(slug: str, features: list[str]):
    org = create_organization(f"Org {slug}", slug)
    org.enabled_features = features
    db.session.flush()
    user = create_user(f"admin-{slug}@test.com", PASSWORD, role="admin", organization_id=org.id)
    user.totp_enabled = True
    lead = LeadService.create(
        {
            "email": f"maija@{slug}.fi",
            "company": "As Oy Testi",
            "first_name": "Maija",
            "last_name": "Mallikas",
            "stage_id": get_default_stage(org.id).id,
        },
        org.id,
        None,
        actor_role="admin",
    )
    db.session.commit()
    return org, user, lead


def _login(client, email: str):
    client.post("/auth/login", data={"email": email, "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True


def test_modules_off_hides_ai_sequences_and_automations(client, app):
    app.config["AI_ENRICHMENT_ENABLED"] = True
    try:
        with app.app_context():
            _, user, lead = _org("plain", [])
            email, lead_id = user.email, lead.id
        _login(client, email)
        page = client.get(f"/leads/{lead_id}").get_data(as_text=True)
        for text in ("AI-havainnot", "Rikasta AI:lla", "AI-myyntisuunnitelma", "AI ei ole vielä analysoinut", 'data-tab="sequences"'):
            assert text not in page, text
        reports = client.get("/reports").get_data(as_text=True)
        assert "AI-rikastus" not in reports
        sidebar = client.get("/dashboard").get_data(as_text=True)
        assert "/sequences" not in sidebar and "/automations" not in sidebar
        assert client.get("/sequences").status_code == 302
        assert client.get("/automations").status_code == 302
    finally:
        app.config["AI_ENRICHMENT_ENABLED"] = False


def test_modules_on_show_them_again(client, app):
    app.config["AI_ENRICHMENT_ENABLED"] = True
    try:
        with app.app_context():
            _, user, lead = _org("full", ["sequences", "automations", "ai_enrichment"])
            email, lead_id = user.email, lead.id
        _login(client, email)
        page = client.get(f"/leads/{lead_id}").get_data(as_text=True)
        assert "AI-havainnot" in page and 'data-tab="sequences"' in page
        assert "AI-rikastus" in client.get("/reports").get_data(as_text=True)
    finally:
        app.config["AI_ENRICHMENT_ENABLED"] = False
