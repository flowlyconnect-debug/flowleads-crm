"""`flask delete-organizations`: hard-delete whole customers, keep the rest."""

from sqlalchemy import text

from app.core.audit import log_audit
from app.extensions import db
from app.leads.models import Lead
from app.leads.purge import delete_organizations
from app.listings.models import Listing, ListingMatch
from app.search.models import SearchProfile
from app.tasks.models import Task
from app.users.models import AuditLog, Organization, User
from app.users.services import create_user
from tests.test_purge_leads import _seed


def _seed_with_superadmin():
    _seed()
    create_user("root@test.com", "securepassword1", role="superadmin", organization_id=None)
    for org in Organization.query.all():
        user = User.query.filter_by(organization_id=org.id).first()
        log_audit("login_success", user_id=user.id, organization_id=org.id)
    db.session.commit()
    db.session.execute(text("PRAGMA foreign_keys=ON"))


def _ids():
    doomed = Organization.query.filter_by(slug="purge-a").one().id
    kept = Organization.query.filter_by(slug="purge-b").one().id
    return doomed, kept


def test_deletes_one_organization_and_nothing_else(app):
    with app.app_context():
        _seed_with_superadmin()
        doomed, kept = _ids()

        counts = delete_organizations([doomed])
        db.session.commit()

        assert counts["organizations"] == 1 and counts["users"] == 1 and counts["leads"] == 1
        assert Organization.query.filter_by(id=doomed).count() == 0
        for model in (User, SearchProfile, Lead, Task, ListingMatch, AuditLog):
            assert model.query.filter_by(organization_id=doomed).count() == 0, model.__name__
        # the other customer, the superadmin and the shared catalogue are untouched
        assert Organization.query.filter_by(id=kept).count() == 1
        assert User.query.filter_by(organization_id=kept).count() == 1
        assert User.query.filter_by(role="superadmin").count() == 1
        assert Lead.query.filter_by(organization_id=kept).count() == 1
        assert Task.query.filter_by(organization_id=kept).count() == 1
        assert SearchProfile.query.filter_by(organization_id=kept).count() == 1
        assert ListingMatch.query.filter_by(organization_id=kept).count() == 2
        assert Listing.query.count() == 2
        # no row anywhere still points at the deleted organization or its rows
        assert db.session.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_cli_needs_yes_and_known_ids(app):
    with app.app_context():
        _seed_with_superadmin()
        doomed, kept = _ids()
    runner = app.test_cli_runner()

    unknown = runner.invoke(args=["delete-organizations", "--id", "9999", "--yes"])
    assert unknown.exit_code == 1

    dry = runner.invoke(args=["delete-organizations", "--id", str(doomed)])
    assert dry.exit_code == 0 and "Kuiva-ajo" in dry.output and "leads: 1" in dry.output
    with app.app_context():
        assert Organization.query.count() == 2 and Lead.query.count() == 2

    real = runner.invoke(args=["delete-organizations", "--id", str(doomed), "--yes"])
    assert real.exit_code == 0 and "organizations: 1" in real.output
    with app.app_context():
        assert [o.id for o in Organization.query.all()] == [kept]
        assert Lead.query.count() == 1
        assert AuditLog.query.filter_by(action="organizations_deleted").count() == 1
