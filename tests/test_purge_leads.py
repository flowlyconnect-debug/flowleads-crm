"""`flask purge-leads`: hard-delete every lead (and optionally the catalogue)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.purge import purge_all_leads
from app.listings import target_services as targets
from app.listings.models import Listing, ListingMatch, ListingRenovation
from app.search.models import SearchProfile
from app.tasks.models import Task
from app.users.models import AuditLog, Organization, User
from tests.test_targets import _listing, _org, _profile


def _seed():
    """Two organizations, each with one lead made from a target, plus a task."""
    for slug in ("purge-a", "purge-b"):
        org, user = _org(slug)
        _profile(org)
    _listing(1)
    _listing(2)
    for org in Organization.query.all():
        match = ListingMatch.query.filter_by(organization_id=org.id).order_by(ListingMatch.id).first()
        lead_id = targets.transfer_targets(org.id, [match.id])["lead_ids"][0]
        user = User.query.filter_by(organization_id=org.id).first()
        db.session.add(
            Task(
                organization_id=org.id,
                lead_id=lead_id,
                assigned_to=user.id,
                created_by=user.id,
                title="Soita",
                due_date=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
    db.session.commit()
    # SQLite ignores foreign keys unless asked; production Postgres enforces
    # them, so the delete order has to be right.
    db.session.execute(text("PRAGMA foreign_keys=ON"))
    assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_dry_run_changes_nothing(app):
    with app.app_context():
        _seed()
        counts = purge_all_leads(with_listings=True, dry_run=True)
        assert counts["leads"] == 2 and counts["listings"] == 2 and counts["tasks"] == 2
        # each match once, although it hangs off both a lead and a listing
        assert counts["listing_matches"] == ListingMatch.query.count() == 4
        assert Lead.query.count() == 2
        assert Listing.query.count() == 2
        assert Task.query.count() == 2


def test_purge_leads_keeps_catalogue_by_default(app):
    with app.app_context():
        _seed()
        purge_all_leads(dry_run=False)
        db.session.commit()
        assert Lead.query.count() == 0
        assert Activity.query.count() == 0
        assert Task.query.count() == 0
        # matches that had become leads are gone, the untouched targets remain
        assert Listing.query.count() == 2
        assert ListingMatch.query.filter_by(status="lead_created").count() == 0
        assert ListingMatch.query.filter_by(status="pending").count() == 2


def test_purge_with_listings_empties_catalogue_but_keeps_customers(app):
    with app.app_context():
        _seed()
        purge_all_leads(with_listings=True, dry_run=False)
        db.session.commit()
        assert Lead.query.count() == 0
        assert Listing.query.count() == 0
        assert ListingMatch.query.count() == 0
        assert ListingRenovation.query.count() == 0
        assert Organization.query.count() == 2
        assert User.query.count() == 2
        assert SearchProfile.query.count() == 2


def test_cli_needs_yes_to_delete(app):
    with app.app_context():
        _seed()
    runner = app.test_cli_runner()

    dry = runner.invoke(args=["purge-leads", "--with-listings"])
    assert dry.exit_code == 0 and "Kuiva-ajo" in dry.output
    with app.app_context():
        assert Lead.query.count() == 2

    real = runner.invoke(args=["purge-leads", "--with-listings", "--yes"])
    assert real.exit_code == 0 and "leads: 2" in real.output
    with app.app_context():
        assert Lead.query.count() == 0 and Listing.query.count() == 0
        assert AuditLog.query.filter_by(action="leads_purged").count() == 1
