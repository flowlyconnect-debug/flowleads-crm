from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.extensions import db
from app.leads.models import Activity, Lead, PipelineStage
from app.leads.services import LeadService, get_default_stage
from app.notifications.models import Notification
from app.proposals.models import Proposal
from app.proposals.pdf import ProposalPDFService
from app.proposals.services import ProposalService, ProposalServiceError
from app.proposals.utils import generate_reference_number, get_sequence_for_year, line_item_total, money
from app.tasks.settings import get_organization_settings
from app.users.services import create_organization, create_user


def _setup_org(app, slug="prop-org"):
    with app.app_context():
        org = create_organization(f"Org {slug}", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@test.com",
            "securepassword1",
            role="admin",
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
        stage = get_default_stage(org.id)
        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "other_org_id": other.id,
            "other_admin_email": other_admin.email,
            "stage_id": stage.id,
        }


def _login(client, email):
    r = client.post("/auth/login", data={"email": email, "password": "securepassword1"})
    assert r.status_code == 302


def _create_lead(app, org_id, stage_id, email="lead@test.com", **kwargs):
    with app.app_context():
        lead = LeadService.create(
            {
                "email": email,
                "first_name": kwargs.get("first_name", "Test"),
                "last_name": kwargs.get("last_name", "Lead"),
                "company": kwargs.get("company", "Acme"),
                "stage_id": stage_id,
            },
            org_id,
            kwargs.get("user_id"),
            actor_role="admin",
        )
        db.session.commit()
        return lead.id


def _proposal_data(**overrides):
    base = {
        "title": "Test proposal",
        "line_items": [
            {
                "description": "Service A",
                "quantity": 2,
                "unit_price": 100,
                "discount_percent": 10,
            },
            {
                "description": "Service B",
                "quantity": 1,
                "unit_price": 50,
                "discount_percent": 0,
            },
        ],
        "tax_percent": 24,
        "discount_percent": 0,
    }
    base.update(overrides)
    return base


# --- unit-style service tests ---


def test_proposal_creation(app):
    ctx = _setup_org(app)
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = ProposalService.create(lead_id, _proposal_data(), ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        assert p.status == "draft"
        assert p.reference_number.startswith("FLW-")
        assert p.lead_name_snapshot
        assert len(p.line_items) == 2
        assert not p.is_locked


def test_total_calculation(app):
    ctx = _setup_org(app)
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = ProposalService.create(lead_id, _proposal_data(), ctx["admin_id"], ctx["org_id"])
        ProposalService.calculate_totals(p)
        # line1: 2*100 - 10% = 180, line2: 50 => subtotal 230
        assert p.subtotal == money(230)
        assert p.total == money(230 * Decimal("1.24"))


def test_tax_calculation(app):
    assert line_item_total(1, 100, 0) == money(100)
    with app.app_context():
        ctx = _setup_org(app, "tax")
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
        p = ProposalService.create(
            lead_id,
            _proposal_data(tax_percent=10, line_items=[{"description": "X", "quantity": 1, "unit_price": 100}]),
            ctx["admin_id"],
            ctx["org_id"],
        )
        ProposalService.calculate_totals(p)
        assert p.total == money(110)


def test_discount_calculation(app):
    with app.app_context():
        ctx = _setup_org(app, "disc")
        lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
        p = ProposalService.create(
            lead_id,
            _proposal_data(
                discount_percent=10,
                line_items=[{"description": "X", "quantity": 1, "unit_price": 200}],
            ),
            ctx["admin_id"],
            ctx["org_id"],
        )
        ProposalService.calculate_totals(p)
        assert p.discount_amount == money(20)
        assert p.total == money((200 - 20) * Decimal("1.24"))


def test_reference_generation(app):
    ctx = _setup_org(app, "ref")
    with app.app_context():
        ref1 = generate_reference_number(ctx["org_id"])
        ref2 = generate_reference_number(ctx["org_id"])
        db.session.commit()
        assert re.match(r"FLW-\d{4}-\d{3}", ref1)
        assert ref1 != ref2
        year = datetime.now(timezone.utc).year
        assert get_sequence_for_year(ctx["org_id"], year) == 2


def test_sequence_reset_by_year(app):
    ctx = _setup_org(app, "year")
    with app.app_context():
        settings = get_organization_settings(ctx["org_id"])
        settings.proposal_sequence_json = {"proposal_sequence_2024": 5}
        db.session.commit()
        assert get_sequence_for_year(ctx["org_id"], 2024) == 5
        assert get_sequence_for_year(ctx["org_id"], 2026) == 0


@patch("app.proposals.services.EmailService.send_to_lead")
def test_send_creates_secure_token(mock_send, app):
    mock_send.return_value = {"status": "sent"}
    ctx = _setup_org(app, "send")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = ProposalService.create(lead_id, _proposal_data(), ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        url = ProposalService.send(p.id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        p2 = db.session.get(Proposal, p.id)
        assert p2.view_token
        assert len(p2.view_token) >= 32
        assert p2.is_locked
        assert p2.status == "sent"
        assert "/p/" in url


def test_token_length_and_randomness(app):
    ctx = _setup_org(app, "tok")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p1 = ProposalService.create(lead_id, _proposal_data(), ctx["admin_id"], ctx["org_id"])
        p2 = ProposalService.create(lead_id, _proposal_data(title="B"), ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        with patch("app.proposals.services.EmailService.send_to_lead", return_value={}):
            ProposalService.send(p1.id, ctx["admin_id"], ctx["org_id"])
            ProposalService.send(p2.id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        t1 = db.session.get(Proposal, p1.id).view_token
        t2 = db.session.get(Proposal, p2.id).view_token
        assert t1 != t2
        assert len(t1) >= 32


def test_public_view_records_viewed_at(app):
    ctx = _setup_org(app, "view")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = ProposalService.create(lead_id, _proposal_data(), ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        with patch("app.proposals.services.EmailService.send_to_lead", return_value={}):
            ProposalService.send(p.id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        token = db.session.get(Proposal, p.id).view_token
        result = ProposalService.record_view(token, {"ip": "1.2.3.4"})
        db.session.commit()
        p2 = db.session.get(Proposal, p.id)
        assert p2.status == "viewed"
        assert p2.viewed_at is not None
        assert result["first_view"] is True


def test_opened_count_increments(app):
    ctx = _setup_org(app, "open")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = ProposalService.create(lead_id, _proposal_data(), ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        with patch("app.proposals.services.EmailService.send_to_lead", return_value={}):
            ProposalService.send(p.id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        token = db.session.get(Proposal, p.id).view_token
        ProposalService.record_view(token, {})
        ProposalService.record_view(token, {})
        db.session.commit()
        assert db.session.get(Proposal, p.id).opened_count == 2


@patch("app.proposals.services.fire_automation_trigger")
def test_accept_changes_status(mock_fire, app):
    ctx = _setup_org(app, "acc")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = _sent_proposal(ctx, lead_id)
        ProposalService.accept(p.view_token, "Jane Doe", {"ip": "10.0.0.1", "user_agent": "Test"})
        db.session.commit()
        p2 = db.session.get(Proposal, p.id)
        assert p2.status == "accepted"


def test_accept_stores_signature_metadata(app):
    ctx = _setup_org(app, "sig")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = _sent_proposal(ctx, lead_id)
        with patch("app.proposals.services.fire_automation_trigger"):
            ProposalService.accept(
                p.view_token, "Jane Doe", {"ip": "10.0.0.1", "user_agent": "Mozilla/5.0"}
            )
        db.session.commit()
        p2 = db.session.get(Proposal, p.id)
        assert p2.signature_name == "Jane Doe"
        assert p2.signature_ip == "10.0.0.1"
        assert "Mozilla" in (p2.signature_user_agent or "")


def test_decline_changes_status(app):
    ctx = _setup_org(app, "dec")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = _sent_proposal(ctx, lead_id)
        with patch("app.proposals.services.fire_automation_trigger"):
            ProposalService.decline(p.view_token, "Too expensive")
        db.session.commit()
        assert db.session.get(Proposal, p.id).status == "declined"


def test_expired_proposals_flagged(app):
    ctx = _setup_org(app, "exp")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = ProposalService.create(
            lead_id,
            _proposal_data(valid_until=(date.today() - timedelta(days=1)).isoformat()),
            ctx["admin_id"],
            ctx["org_id"],
        )
        db.session.commit()
        with patch("app.proposals.services.EmailService.send_to_lead", return_value={}):
            ProposalService.send(p.id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        count = ProposalService.expire_old_proposals()
        db.session.commit()
        assert count >= 1
        assert db.session.get(Proposal, p.id).status == "expired"


def test_draft_only_edit_enforcement(app):
    ctx = _setup_org(app, "edit")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = _sent_proposal(ctx, lead_id)
        with pytest.raises(ProposalServiceError) as exc:
            ProposalService.update(p.id, {"title": "Hack"}, ctx["org_id"], ctx["admin_id"])
        assert exc.value.code == "locked"


def test_locked_proposal_cannot_edit(app):
    test_draft_only_edit_enforcement(app)


def test_duplicate_creates_editable_draft(app):
    ctx = _setup_org(app, "dup")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = _sent_proposal(ctx, lead_id)
        copy = ProposalService.duplicate(p.id, ctx["org_id"], ctx["admin_id"])
        db.session.commit()
        assert copy.id != p.id
        assert copy.status == "draft"
        assert copy.is_editable
        assert copy.view_token is None
        assert copy.reference_number != p.reference_number


def test_public_route_without_auth(client, app):
    ctx = _setup_org(app, "pub")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = _sent_proposal(ctx, lead_id)
        db.session.commit()
        token = p.view_token
    r = client.get(f"/p/{token}")
    assert r.status_code == 200
    assert b"reference" in r.data.lower() or p.reference_number.encode() in r.data


def test_cross_tenant_token_isolation(app):
    ctx = _setup_org(app, "xten")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        other_stage_id = get_default_stage(ctx["other_org_id"]).id
    other_lead = _create_lead(app, ctx["other_org_id"], other_stage_id, email="o@test.com")
    with app.app_context():
        p = _sent_proposal(ctx, lead_id)
        other_p = ProposalService.create(
            other_lead, _proposal_data(), None, ctx["other_org_id"]
        )
        db.session.commit()
        with patch("app.proposals.services.EmailService.send_to_lead", return_value={}):
            ProposalService.send(other_p.id, None, ctx["other_org_id"])
        db.session.commit()
        token_a = db.session.get(Proposal, p.id).view_token
        token_b = db.session.get(Proposal, other_p.id).view_token
        assert ProposalService.record_view(token_a, {})["proposal"].organization_id == ctx["org_id"]
        assert (
            ProposalService.record_view(token_b, {})["proposal"].organization_id
            == ctx["other_org_id"]
        )


def test_pdf_generation_works(app):
    ctx = _setup_org(app, "pdf")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = ProposalService.create(lead_id, _proposal_data(), ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        content = ProposalPDFService.generate(p)
        assert content
        assert len(content) > 100


@patch("app.proposals.services.fire_automation_trigger")
def test_automation_triggers_fire(mock_fire, app):
    ctx = _setup_org(app, "auto")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = _sent_proposal(ctx, lead_id)
        ProposalService.record_view(p.view_token, {})
        db.session.commit()
        assert any(c[0][0] == "proposal_viewed" for c in mock_fire.call_args_list)


def test_lead_moved_to_won_on_accept(app):
    ctx = _setup_org(app, "won")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        settings = get_organization_settings(ctx["org_id"])
        settings.proposal_move_lead_to_won_on_accept = True
        p = _sent_proposal(ctx, lead_id)
        with patch("app.proposals.services.fire_automation_trigger"):
            ProposalService.accept(p.view_token, "Buyer", {})
        db.session.commit()
        lead = db.session.get(Lead, lead_id)
        won = PipelineStage.query.filter_by(organization_id=ctx["org_id"], name="Won").first()
        assert lead.stage_id == won.id
        assert lead.status == "won"


def test_notifications_created(app):
    ctx = _setup_org(app, "notif")
    with app.app_context():
        lead = LeadService.create(
            {
                "email": "n@test.com",
                "company": "Co",
                "stage_id": ctx["stage_id"],
                "assigned_to": ctx["admin_id"],
            },
            ctx["org_id"],
            ctx["admin_id"],
            actor_role="admin",
        )
        db.session.commit()
        lead_id = lead.id
    with app.app_context():
        p = _sent_proposal(ctx, lead_id)
        with patch("app.proposals.services.fire_automation_trigger"):
            ProposalService.accept(p.view_token, "Buyer", {})
        db.session.commit()
        n = Notification.query.filter_by(
            user_id=ctx["admin_id"], type="proposal_accepted"
        ).first()
        assert n is not None


def test_scheduler_expiration_works(app):
    ctx = _setup_org(app, "sched")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = ProposalService.create(
            lead_id,
            _proposal_data(valid_until=(date.today() - timedelta(days=2)).isoformat()),
            ctx["admin_id"],
            ctx["org_id"],
        )
        p.status = "viewed"
        p.is_locked = True
        db.session.commit()
        count = ProposalService.expire_old_proposals()
        db.session.commit()
        assert count == 1


def test_activity_logged_on_send(app):
    ctx = _setup_org(app, "act")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        p = ProposalService.create(lead_id, _proposal_data(), ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        with patch("app.proposals.services.EmailService.send_to_lead", return_value={}):
            ProposalService.send(p.id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        act = Activity.query.filter_by(lead_id=lead_id, type="proposal_sent").first()
        assert act is not None


def _sent_proposal(ctx, lead_id):
    p = ProposalService.create(lead_id, _proposal_data(), ctx["admin_id"], ctx["org_id"])
    db.session.commit()
    with patch("app.proposals.services.EmailService.send_to_lead", return_value={}):
        ProposalService.send(p.id, ctx["admin_id"], ctx["org_id"])
    db.session.commit()
    return db.session.get(Proposal, p.id)
