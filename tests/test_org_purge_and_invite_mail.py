"""Deleting customer organizations and seeing why an invite e-mail failed."""

from __future__ import annotations

from unittest.mock import patch

from app.admin.org_purge import OrganizationPurgeError, customer_organization_ids, purge_organization
from app.admin.onboarding_services import create_customer
from app.core.audit import AuditLog
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.leads.models import Lead
from app.listings import target_services as targets
from app.listings.import_services import import_listings
from app.listings.models import Listing, ListingMatch
from app.listings.services import rematch_profiles
from app.search.models import SearchProfile
from app.users.models import Organization, User
from app.users.services import create_user

PASSWORD = "temporarypassword1"
PIPE = "Putkiremontti"


def _superadmin():
    user = create_user("sa-purge@test.com", PASSWORD, role="superadmin")
    user.totp_enabled = True
    db.session.commit()
    return user


def _customer(actor, slug):
    result = create_customer(
        actor=actor,
        organization_name=f"Asiakas {slug}",
        admin_email=f"admin-{slug}@asiakas.fi",
        admin_password=PASSWORD,
        remonttityypit=[PIPE],
    )
    result.admin_user.must_change_password = False
    db.session.commit()
    return result


def _with_data(actor):
    a = _customer(actor, "a")
    b = _customer(actor, "b")
    create_user("myyja-a@asiakas.fi", PASSWORD, role="user", organization_id=a.organization.id)
    import_listings(
        [
            {"taloyhtio": "As Oy Yksi", "maakunta": "Uusimaa", "kaupunki": "Espoo", "remonttityyppi": PIPE},
            {"taloyhtio": "As Oy Kaksi", "maakunta": "Lappi", "kaupunki": "Oulu", "remonttityyppi": PIPE},
        ]
    )
    rematch_profiles()
    match = ListingMatch.query.filter_by(organization_id=a.organization.id).first()
    targets.transfer_targets(a.organization.id, [match.id])
    db.session.commit()
    return a, b


def test_purge_removes_the_organization_and_only_it(app):
    with app.app_context():
        actor = _superadmin()
        a, b = _with_data(actor)
        a_id, b_id = a.organization.id, b.organization.id
        assert Lead.query.filter_by(organization_id=a_id).count() == 1

        dry = purge_organization(a_id)
        assert dry["organizations"] == 1 and dry["users"] == 2
        assert db.session.get(Organization, a_id) is not None  # dry run changed nothing

        purge_organization(a_id, dry_run=False)
        db.session.commit()
        assert db.session.get(Organization, a_id) is None
        assert User.query.filter_by(organization_id=a_id).count() == 0
        assert User.query.filter(User.email.like("%-a@asiakas.fi")).count() == 0
        assert SearchProfile.query.filter_by(organization_id=a_id).count() == 0
        assert ListingMatch.query.filter_by(organization_id=a_id).count() == 0
        assert Lead.query.filter_by(organization_id=a_id).count() == 0
        # the other customer, the superadmin and the shared catalogue stay
        assert db.session.get(Organization, b_id) is not None
        assert ListingMatch.query.filter_by(organization_id=b_id).count() == 2
        assert db.session.get(User, actor.id) is not None
        assert Listing.query.count() == 2


def test_all_customers_and_superadmin_guard(app):
    with app.app_context():
        actor = _superadmin()
        a, b = _with_data(actor)
        assert customer_organization_ids() == [a.organization.id, b.organization.id]
        actor.organization_id = b.organization.id
        db.session.commit()
        assert customer_organization_ids() == [a.organization.id]
        try:
            purge_organization(b.organization.id, dry_run=False)
            raise AssertionError("expected OrganizationPurgeError")
        except OrganizationPurgeError:
            db.session.rollback()


def _login_superadmin(client, app):
    with app.app_context():
        actor = _superadmin()
        a, _ = _with_data(actor)
        org_id, name = a.organization.id, a.organization.name
    client.post("/auth/login", data={"email": "sa-purge@test.com", "password": PASSWORD})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    return org_id, name


def test_delete_button_needs_the_typed_name(app, client):
    org_id, name = _login_superadmin(client, app)
    assert "Poista pysyvästi" in client.get("/admin/customers").get_data(as_text=True)
    client.post(f"/admin/customers/{org_id}/delete", data={"confirm_name": "väärä"})
    with app.app_context():
        assert db.session.get(Organization, org_id) is not None
    client.post(f"/admin/customers/{org_id}/delete", data={"confirm_name": name})
    with app.app_context():
        assert db.session.get(Organization, org_id) is None
        assert AuditLog.query.filter_by(action="organization_deleted").count() == 1


def test_cli_dry_run_then_delete(app, runner):
    with app.app_context():
        actor = _superadmin()
        _with_data(actor)
    out = runner.invoke(args=["purge-organizations", "--all-customers"]).output
    assert "kuiva-ajo" in out
    with app.app_context():
        assert Organization.query.count() == 2
    runner.invoke(args=["purge-organizations", "--all-customers", "--yes"])
    with app.app_context():
        assert Organization.query.count() == 0
        assert User.query.filter_by(role="superadmin").count() == 1


def test_failed_customer_invite_mail_shows_the_reason(app, client):
    with app.app_context():
        _superadmin()
    client.post("/auth/login", data={"email": "sa-purge@test.com", "password": PASSWORD})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    reason = "Sandbox subdomains are for test purposes only."
    with patch("app.email.services._mailgun_send", return_value=(False, None, reason)):
        page = client.post(
            "/admin/customers/invite",
            data={"organization_name": "Posti Oy", "admin_email": "posti@asiakas.fi", "remonttityypit": [PIPE]},
            follow_redirects=True,
        ).get_data(as_text=True)
    assert "sähköposti ei lähtenyt" in page and reason in page
    with app.app_context():
        entry = AuditLog.query.filter_by(action="account_email_failed").one()
        assert entry.metadata_json["kind"] == "customer_invite"
        assert reason in entry.metadata_json["error"]


def test_sent_invite_is_logged(app, client):
    with app.app_context():
        _superadmin()
    client.post("/auth/login", data={"email": "sa-purge@test.com", "password": PASSWORD})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    with patch("app.email.services._mailgun_send", return_value=(True, "<id@mg>", None)):
        page = client.post(
            "/admin/customers/invite",
            data={"organization_name": "Lahti Oy", "admin_email": "lahti@asiakas.fi", "remonttityypit": [PIPE]},
            follow_redirects=True,
        ).get_data(as_text=True)
    assert "Kutsu lähti sähköpostilla" in page
    with app.app_context():
        assert AuditLog.query.filter_by(action="account_email_sent").count() == 1
