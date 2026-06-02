import json
from unittest.mock import patch

import pytest

from app.companies.models import Company, Contact, lead_contacts
from app.extensions import db
from app.leads.models import Lead, PipelineStage
from app.leads.services import LeadService, get_default_stage
from app.users.services import create_organization, create_user


def _setup_org_with_users(app, slug="org-a"):
    with app.app_context():
        org = create_organization("Org A", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@acme.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        user = create_user(
            f"user-{slug}@acme.com",
            "securepassword1",
            role="user",
            organization_id=org.id,
        )
        other_org = create_organization("Org B", f"{slug}-b")
        db.session.flush()
        other_user = create_user(
            f"user-{slug}-b@acme.com",
            "securepassword1",
            role="user",
            organization_id=other_org.id,
        )
        db.session.commit()
        return {
            "org_id": org.id,
            "admin_email": admin.email,
            "user_email": user.email,
            "admin_id": admin.id,
            "user_id": user.id,
            "other_org_id": other_org.id,
            "other_user_id": other_user.id,
        }


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return response


def _create_lead(app, org_id, stage_id, user_id=None, **kwargs):
    with app.app_context():
        data = {
            "email": kwargs.get("email", "lead@example.com"),
            "company": kwargs.get("company"),
            "first_name": kwargs.get("first_name"),
            "source": kwargs.get("source", "manual"),
            "source_ref": kwargs.get("source_ref"),
            "score": kwargs.get("score"),
            "stage_id": stage_id,
            "assigned_to": kwargs.get("assigned_to"),
        }
        lead = LeadService.create(data, org_id, user_id, actor_role="admin")
        db.session.commit()
        return lead


def _create_company(app, org_id, name="MyCo", type_="prospect"):
    with app.app_context():
        company = Company(organization_id=org_id, name=name, type=type_)
        db.session.add(company)
        db.session.flush()
        company_id = company.id
        db.session.commit()
        return company_id


def _setup_org_api(app, slug="api-org"):
    with app.app_context():
        org = create_organization(f"Org {slug}", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        user = create_user(
            f"user-{slug}@test.com",
            "securepassword1",
            role="user",
            organization_id=org.id,
        )
        other = create_organization(f"Other {slug}", f"{slug}-other")
        db.session.flush()
        other_admin = create_user(
            f"admin-other-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=other.id,
        )
        db.session.commit()
        return {
            "org_id": org.id,
            "other_org_id": other.id,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "user_email": user.email,
            "other_admin_email": other_admin.email,
        }


def _auth_headers(key: str, use_bearer: bool = True):
    if use_bearer:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    return {"X-API-Key": key, "Content-Type": "application/json"}


def _post_lead(client, key, payload, **kwargs):
    return client.post(
        "/api/v1/leads",
        data=json.dumps(payload),
        headers=_auth_headers(key),
        **kwargs,
    )


# --- test cases requested ---


def test_company_org_scoping(app, client):
    ctx = _setup_org_with_users(app, "co-scope")
    company_id = _create_company(app, ctx["org_id"], name="SecretCo")

    _login(client, "user-co-scope-b@acme.com")
    response = client.get(f"/companies/{company_id}")
    assert response.status_code == 404


def test_company_auto_link(app, client):
    ctx = _setup_org_api(app, "auto-link")

    # Create API key for org A
    from app.api.services import create_api_key

    with app.app_context():
        api_key_a, full_key_a = create_api_key(ctx["org_id"], "k-a", test_mode=True)
        api_key_a_id = api_key_a.id
        api_key_b, full_key_b = create_api_key(ctx["other_org_id"], "k-b", test_mode=True)
        api_key_b_id = api_key_b.id
        db.session.commit()

    payload1 = {"email": "c1@example.com", "company": "Yritys Oy", "source": "manual"}
    resp1 = _post_lead(client, full_key_a, payload1)
    assert resp1.status_code in (200, 201)

    with app.app_context():
        company = Company.query.filter_by(organization_id=ctx["org_id"], name="Yritys Oy").first()
        assert company is not None
        lead = Lead.query.filter_by(organization_id=ctx["org_id"], email="c1@example.com").first()
        assert lead is not None
        assert lead.company_id == company.id

        # Create second lead with different casing: must reuse existing company
        payload2 = {"email": "c2@example.com", "company": "yritys oy", "source": "manual"}
        resp2 = _post_lead(client, full_key_a, payload2)
        assert resp2.status_code in (200, 201)

        companies = Company.query.filter_by(organization_id=ctx["org_id"]).all()
        assert len(companies) == 1

        lead2 = Lead.query.filter_by(organization_id=ctx["org_id"], email="c2@example.com").first()
        assert lead2 is not None
        assert lead2.company_id == company.id

        # Must not create company in org B
        payload3 = {"email": "c3@example.com", "company": "Yritys Oy", "source": "manual"}
        resp3 = _post_lead(client, full_key_a, payload3)
        assert resp3.status_code in (200, 201)

        other_companies = Company.query.filter_by(organization_id=ctx["other_org_id"]).all()
        assert other_companies == []


def test_contact_lead_association(app, client):
    ctx = _setup_org_with_users(app, "contact-assoc")
    with app.app_context():
        stage = get_default_stage(ctx["org_id"])
        other_stage = get_default_stage(ctx["other_org_id"])

        company_id = _create_company(app, ctx["org_id"], name="Contoso")

        lead_a = LeadService.create(
            {
                "email": "a1@a.com",
                "company": "Contoso",
                "stage_id": stage.id,
                "source": "manual",
                "assigned_to": ctx["admin_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        lead_b = LeadService.create(
            {
                "email": "b1@b.com",
                "company": "Contoso",
                "stage_id": other_stage.id,
                "source": "manual",
                "assigned_to": ctx["other_user_id"],
            },
            ctx["other_org_id"],
            ctx["other_user_id"],
            actor_role="admin",
        )
        db.session.commit()

        contact = Contact(
            organization_id=ctx["org_id"],
            company_id=company_id,
            first_name="Alice",
            last_name="Smith",
            email="alice@example.com",
        )
        db.session.add(contact)
        db.session.flush()

        # Directly create cross-tenant association row to simulate inconsistent data.
        db.session.execute(
            lead_contacts.insert().values(lead_id=lead_a.id, contact_id=contact.id)
        )
        db.session.execute(
            lead_contacts.insert().values(lead_id=lead_b.id, contact_id=contact.id)
        )
        db.session.commit()

        contact_ref = db.session.get(Contact, contact.id)
        lead_ids = {l.id for l in contact_ref.leads}
        assert lead_a.id in lead_ids
        assert lead_b.id not in lead_ids

        # Removing a lead must not delete the contact.
        LeadService.archive(lead_a.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        assert db.session.get(Contact, contact.id) is not None


def test_company_lead_count(app, client):
    ctx = _setup_org_with_users(app, "lead-count")
    with app.app_context():
        stage_a = get_default_stage(ctx["org_id"])
        won = PipelineStage.query.filter_by(organization_id=ctx["org_id"], name="Voitettu").first()
        lost = PipelineStage.query.filter_by(organization_id=ctx["org_id"], name="Hävitty").first()
        assert won is not None
        assert lost is not None

    company_id = _create_company(app, ctx["org_id"], name="OpenCo")

    with app.app_context():
        lead_active = LeadService.create(
            {
                "email": "active@co.com",
                "company": "OpenCo",
                "stage_id": stage_a.id,
                "source": "manual",
                "assigned_to": ctx["admin_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        lead_active.company_id = company_id

        # Won lead
        lead_won = LeadService.create(
            {
                "email": "won@co.com",
                "company": "OpenCo",
                "stage_id": stage_a.id,
                "source": "manual",
                "assigned_to": ctx["admin_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        lead_won.company_id = company_id
        LeadService.move_stage(lead_won.id, won.id, ctx["org_id"], ctx["admin_id"])

        # Lost lead
        lead_lost = LeadService.create(
            {
                "email": "lost@co.com",
                "company": "OpenCo",
                "stage_id": stage_a.id,
                "source": "manual",
                "assigned_to": ctx["admin_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        lead_lost.company_id = company_id
        LeadService.move_stage(
            lead_lost.id,
            lost.id,
            ctx["org_id"],
            ctx["admin_id"],
            lost_reason="Ei budjettia",
        )

        # Archived lead
        lead_archived = LeadService.create(
            {
                "email": "arch@co.com",
                "company": "OpenCo",
                "stage_id": stage_a.id,
                "source": "manual",
                "assigned_to": ctx["admin_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        lead_archived.company_id = company_id
        LeadService.archive(lead_archived.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()

    _login(client, ctx["admin_email"])
    response = client.get(f"/companies/{company_id}")
    assert response.status_code == 200
    assert b"2" in response.data  # open = active + won (not lost/archived)


def test_companies_and_contacts_new_routes(app, client):
    ctx = _setup_org_with_users(app, "co-new")
    _login(client, ctx["admin_email"])

    company_get = client.get("/companies/new")
    assert company_get.status_code == 200
    assert b"Lis\xc3\xa4\xc3\xa4 yritys" in company_get.data or b"Lisaa yritys" in company_get.data

    company_post = client.post(
        "/companies/new",
        data={"name": "Uusi Oy", "type": "prospect"},
        follow_redirects=False,
    )
    assert company_post.status_code == 302

    with app.app_context():
        company = Company.query.filter_by(organization_id=ctx["org_id"], name="Uusi Oy").first()
        assert company is not None

    contact_get = client.get("/contacts/new")
    assert contact_get.status_code == 200

    contact_post = client.post(
        "/contacts/new",
        data={
            "first_name": "Matti",
            "last_name": "Meikalainen",
            "email": "matti@uusi.fi",
            "company_id": company.id,
        },
        follow_redirects=False,
    )
    assert contact_post.status_code == 302

    with app.app_context():
        contact = Contact.query.filter_by(organization_id=ctx["org_id"], email="matti@uusi.fi").first()
        assert contact is not None
        assert contact.company_id == company.id


def test_companies_superadmin_without_org_redirects(app, client):
    with app.app_context():
        create_user(
            "super-co@test.com",
            "securepassword1",
            role="superadmin",
            organization_id=None,
        )
        db.session.commit()

    _login(client, "super-co@test.com")
    response = client.get("/companies", follow_redirects=False)
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]


def test_companies_filter(app, client):
    ctx = _setup_org_with_users(app, "co-filter")
    _create_company(app, ctx["org_id"], name="Asiakas Oy", type_="customer")
    _create_company(app, ctx["org_id"], name="Prospekti Oy", type_="prospect")
    _create_company(app, ctx["other_org_id"], name="Foreign Oy", type_="customer")

    with app.app_context():
        stage = get_default_stage(ctx["org_id"])
        lost = PipelineStage.query.filter_by(organization_id=ctx["org_id"], name="Hävitty").first()
        company_id = Company.query.filter_by(organization_id=ctx["org_id"], name="Asiakas Oy").first().id

        lead_open = LeadService.create(
            {
                "email": "open@asiakas.fi",
                "company": "Asiakas Oy",
                "stage_id": stage.id,
                "source": "manual",
            },
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        lead_open.company_id = company_id

        lead_lost = LeadService.create(
            {
                "email": "lost@asiakas.fi",
                "company": "Asiakas Oy",
                "stage_id": stage.id,
                "source": "manual",
            },
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        lead_lost.company_id = company_id
        LeadService.move_stage(
            lead_lost.id,
            lost.id,
            ctx["org_id"],
            ctx["admin_id"],
            lost_reason="Ei budjettia",
        )
        db.session.commit()

    _login(client, ctx["admin_email"])

    with patch("app.companies.routes.CLIENT_FILTER_THRESHOLD", 0):
        customer_resp = client.get("/companies?type=customer")
        assert customer_resp.status_code == 200
        body = customer_resp.get_data(as_text=True)
        assert "Asiakas Oy" in body
        assert "Prospekti Oy" not in body
        assert "Foreign Oy" not in body

        list_resp = client.get("/companies")
        list_body = list_resp.get_data(as_text=True)
        assert "1 liidi" in list_body
        assert "2 liidi" not in list_body

        search_resp = client.get("/companies?q=Prospekti")
        search_body = search_resp.get_data(as_text=True)
        assert "Prospekti Oy" in search_body
        assert "Asiakas Oy" not in search_body

    client.get("/auth/logout", follow_redirects=True)
    _login(client, "user-co-filter-b@acme.com")
    cross_resp = client.get("/companies")
    cross_body = cross_resp.get_data(as_text=True)
    assert "Foreign Oy" in cross_body
    assert "Asiakas Oy" not in cross_body
    assert "Prospekti Oy" not in cross_body

