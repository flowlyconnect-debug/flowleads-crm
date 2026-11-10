"""Contact-level dedup: leads that share an email, or a name within the
same company, should link to one shared Contact instead of each lead
carrying its own disconnected copy of the person's details."""
from app.api.services import upsert_lead
from app.companies.models import Contact
from app.extensions import db
from app.users.services import create_organization


def _setup_org(app, slug="contact-dedup-org"):
    with app.app_context():
        org = create_organization("Contact Dedup Org", slug)
        db.session.commit()
        return org.id


def test_two_leads_with_same_email_share_one_contact(app):
    org_id = _setup_org(app)
    with app.app_context():
        lead1, _ = upsert_lead(
            org_id,
            {"email": "ada@example.com", "first_name": "Ada", "source": "n8n", "source_ref": "ref-1"},
        )
        db.session.commit()
        lead2, _ = upsert_lead(
            org_id,
            {"email": "ADA@Example.com", "first_name": "Ada", "source": "n8n", "source_ref": "ref-2"},
        )
        db.session.commit()

        assert Contact.query.filter_by(organization_id=org_id).count() == 1
        contact = Contact.query.filter_by(organization_id=org_id).one()
        assert {c.id for c in lead1.contacts} == {contact.id}
        assert {c.id for c in lead2.contacts} == {contact.id}


def test_lead_without_email_matches_by_name_and_company(app):
    org_id = _setup_org(app)
    with app.app_context():
        lead1, _ = upsert_lead(
            org_id,
            {
                "first_name": "Bob",
                "last_name": "Builder",
                "company": "Acme Oy",
                "source": "n8n",
                "source_ref": "ref-a",
            },
        )
        db.session.commit()
        lead2, _ = upsert_lead(
            org_id,
            {
                "first_name": "Bob",
                "last_name": "Builder",
                "company": "Acme Oy",
                "source": "n8n",
                "source_ref": "ref-b",
            },
        )
        db.session.commit()

        assert Contact.query.filter_by(organization_id=org_id).count() == 1
        contact = Contact.query.filter_by(organization_id=org_id).one()
        assert {c.id for c in lead1.contacts} == {contact.id}
        assert {c.id for c in lead2.contacts} == {contact.id}


def test_different_emails_create_separate_contacts(app):
    org_id = _setup_org(app)
    with app.app_context():
        upsert_lead(
            org_id,
            {"email": "dana@example.com", "first_name": "Dana", "source": "n8n", "source_ref": "ref-d1"},
        )
        db.session.commit()
        upsert_lead(
            org_id,
            {"email": "erik@example.com", "first_name": "Erik", "source": "n8n", "source_ref": "ref-d2"},
        )
        db.session.commit()

        assert Contact.query.filter_by(organization_id=org_id).count() == 2


def test_updating_lead_fills_in_missing_contact_fields(app):
    org_id = _setup_org(app)
    with app.app_context():
        lead, _ = upsert_lead(
            org_id,
            {"email": "carol@example.com", "first_name": "Carol", "source": "n8n", "source_ref": "ref-c"},
        )
        db.session.commit()
        contact = Contact.query.filter_by(organization_id=org_id).one()
        assert contact.phone is None

        upsert_lead(
            org_id,
            {
                "email": "carol@example.com",
                "first_name": "Carol",
                "phone": "+358401234567",
                "source": "n8n",
                "source_ref": "ref-c",
            },
        )
        db.session.commit()

        db.session.refresh(contact)
        assert contact.phone == "+358401234567"
