"""Every GET route of the app, as seller, admin and superadmin, on an
organization with real data: no page may answer 5xx or show the error page.
A new route is covered automatically; give its URL parameter a value in
`_params()` if it takes one that is not there yet."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db

PASSWORD = "securepassword1"
NOW = datetime.now(timezone.utc)
PARAM = re.compile(r"<(?:[a-z]+:)?([a-z_]+)>")
SKIP_ENDPOINTS = {"static", "auth.logout"}


def _seed():
    from app.auth.services import generate_totp_secret, normalize_totp_secret
    from app.automations.services import AutomationService
    from app.companies.models import Company
    from app.forms.services import WebFormService
    from app.leads.services import LeadService, get_default_stage
    from app.listings import target_services
    from app.listings.models import Listing, ListingMatch, ListingRenovation
    from app.proposals.services import ProposalService
    from app.search.models import SearchProfile
    from app.segments.services import SegmentService
    from app.sequences.defaults import ensure_default_sequences
    from app.tasks.services import TaskService
    from app.users.services import create_organization, create_user

    org = create_organization("Sweep Oy", "sweep-oy")
    other = create_organization("Sweep Other", "sweep-other")
    db.session.flush()
    admin = create_user("sweep-admin@test.com", PASSWORD, role="admin", organization_id=org.id)
    seller = create_user("sweep-user@test.com", PASSWORD, role="user", organization_id=org.id)
    superadmin = create_user("sweep-super@test.com", PASSWORD, role="superadmin", organization_id=None)
    for user in (admin, seller, superadmin):
        user.totp_secret = normalize_totp_secret(generate_totp_secret())
        user.totp_enabled = True
    profile = SearchProfile(
        organization_id=org.id, name="P", remonttityyppi="Putkiremontti", regions=["Uusimaa"],
        source="oikotie", is_active=True, min_confidence=60, needs_rematch=False,
    )
    db.session.add(profile)
    db.session.flush()
    match_ids = []
    for i in range(1, 4):
        listing = Listing(
            source="import", external_id=f"sweep-{i}", maakunta="Uusimaa", kaupunki="Espoo",
            osoite=f"Testitie {i}", taloyhtio=f"As Oy Sweep {i}", analysis_status="done",
            first_seen_at=NOW, last_seen_at=NOW, published_at=NOW, build_year=1970,
            building_type="kerrostalo", isannoitsija="Isännöinti Kallio Oy",
            isannoitsija_email=f"toimisto{i}@kallio.fi",
        )
        db.session.add(listing)
        db.session.flush()
        db.session.add(ListingRenovation(listing_id=listing.id, remonttityyppi="Putkiremontti", confidence=90, timing="2027"))
        match = ListingMatch(
            organization_id=org.id, profile_id=profile.id, listing_id=listing.id, status="pending",
            confidence=90, created_at=NOW, released_at=NOW,
        )
        db.session.add(match)
        db.session.flush()
        match_ids.append(match.id)
    db.session.commit()
    target_services.transfer_targets(org.id, match_ids[:1], assigned_to=seller.id)
    lead = LeadService.create(
        {"email": "sweep-lead@test.com", "first_name": "Liisa", "company": "Acme", "stage_id": get_default_stage(org.id).id},
        org.id, admin.id, actor_role="admin",
    )
    db.session.flush()
    ensure_default_sequences(org.id, created_by=admin.id)
    ProposalService.create(
        lead.id, {"title": "T", "line_items": [{"description": "A", "quantity": 1, "unit_price": 100}], "tax_percent": 24},
        admin.id, org.id,
    )
    TaskService.create(
        {"title": "Soita", "type": "call", "due_date": (NOW + timedelta(days=1)).isoformat(), "assigned_to": seller.id},
        org.id, admin.id, lead_id=lead.id, actor_role="admin",
    )
    WebFormService.create_form(
        {"name": "C", "title": "Ota yhteyttä", "fields": [{"key": "email", "label": "Email", "type": "email", "required": True}],
         "notify_users": [], "is_active": True},
        org.id, admin.id,
    )
    AutomationService.create(
        {"name": "A", "trigger_type": "lead_created", "trigger_config": {}, "is_active": True,
         "actions": [{"action_type": "create_task", "action_config": {"title": "x", "due_days": 1, "assign_to": "owner"}}]},
        org.id, admin.id,
    )
    SegmentService.save("S", {"status": "new"}, org.id, user_id=admin.id)
    target_services.save_search(org.id, admin.id, "Espoo", {"city": "Espoo"})
    db.session.add(Company(organization_id=org.id, name="MyCo", type="prospect"))
    db.session.commit()
    return org.id, other.id, lead.id, seller.id


def _first(model, attr="id", org_id=None):
    query = model.query
    if org_id is not None and hasattr(model, "organization_id"):
        query = query.filter_by(organization_id=org_id)
    row = query.order_by(model.id).first()
    return getattr(row, attr) if row else None


def _params(org_id, other_org_id, lead_id, seller_id):
    from app.automations.models import Automation
    from app.companies.models import Company
    from app.custom_fields.models import CustomFieldDefinition
    from app.email.models import EmailTemplate
    from app.forms.models import WebForm
    from app.listings.models import Listing, SavedTargetSearch
    from app.proposals.models import Proposal
    from app.segments.models import Segment
    from app.sequences.models import EmailSequence, EmailSequenceStep
    from app.tasks.models import Task
    from app.webhooks.models import WebhookEndpoint

    sequence_id = _first(EmailSequence, org_id=org_id)
    step = EmailSequenceStep.query.filter_by(sequence_id=sequence_id).first()
    return {
        "lead_id": lead_id,
        "organization_id": other_org_id,
        "user_id": seller_id,
        "listing_id": _first(Listing),
        "automation_id": _first(Automation, org_id=org_id),
        "company_id": _first(Company, org_id=org_id),
        "field_id": _first(CustomFieldDefinition, org_id=org_id),
        "form_id": _first(WebForm, org_id=org_id),
        "form_token": _first(WebForm, "form_token", org_id=org_id),
        "proposal_id": _first(Proposal, org_id=org_id),
        "segment_id": _first(Segment, org_id=org_id),
        "sequence_id": sequence_id,
        "step_id": step.id if step else None,
        "task_id": _first(Task, org_id=org_id),
        "template_id": _first(EmailTemplate),
        "search_id": _first(SavedTargetSearch, org_id=org_id),
        "endpoint_id": _first(WebhookEndpoint, org_id=org_id),
        "provider": "hubspot",
        "filename": "missing.sql.gz",
        "token": "invalid-token",
        "view_token": "invalid-token",
    }


@pytest.mark.parametrize("email", ["sweep-user@test.com", "sweep-admin@test.com", "sweep-super@test.com"])
def test_every_page_renders_without_server_error(app, email):
    org_id, other_org_id, lead_id, seller_id = _seed()
    params = _params(org_id, other_org_id, lead_id, seller_id)
    rules = [r for r in app.url_map.iter_rules() if "GET" in r.methods and r.endpoint not in SKIP_ENDPOINTS]
    db.session.remove()

    client = app.test_client()
    response = client.post("/auth/login", data={"email": email, "password": PASSWORD})
    assert response.status_code == 302
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True

    failures = []
    for rule in sorted(rules, key=lambda r: r.rule):
        url = PARAM.sub(lambda m: str(params.get(m.group(1)) or 999999), rule.rule)
        if email.startswith("sweep-super") and not url.startswith("/admin"):
            url += f"?organization_id={org_id}"
        response = client.get(url, follow_redirects=True)
        body = response.get_data(as_text=True) if response.mimetype and "text" in response.mimetype else ""
        if response.status_code >= 500 or "Something went wrong" in body or "An unexpected error occurred" in body:
            failures.append(f"{url} -> {response.status_code}")
        if response.request.path.startswith("/auth/login"):
            failures.append(f"{url} -> logged out")
    assert not failures, failures
