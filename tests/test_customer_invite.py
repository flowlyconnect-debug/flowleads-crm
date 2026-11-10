from unittest.mock import patch

import pytest

from app.admin.invite_services import (
    InviteServiceError,
    accept_invite,
    activate_source,
    create_invite,
    get_valid_invite,
    renew_invite,
)
from app.admin.models import CustomerInvite
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.search.constants import ALL_FINLAND_REGION
from app.search.models import SearchJob, SearchProfile
from app.users.models import AuditLog, Organization, User
from app.users.services import create_user


def _superadmin(app):
    with app.app_context():
        user = create_user("sa-invite@test.com", "securepassword1", role="superadmin")
        user.totp_enabled = True
        db.session.commit()
        return user.id


def _login_superadmin(client, app):
    _superadmin(app)
    client.post(
        "/auth/login",
        data={"email": "sa-invite@test.com", "password": "securepassword1"},
    )
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True


# --- Service-level ---


def test_create_invite_creates_org_without_admin_or_profile(app):
    with app.app_context():
        actor = create_user("actor-invite1@test.com", "securepassword1", role="superadmin")
        db.session.commit()

        result = create_invite(
            actor=actor,
            organization_name="Kutsu Oy",
            admin_email="uusi.admin@kutsu.fi",
            search_profile_name="Lukkoremontti",
            remonttityyppi="Lukkoremontti / kulunvalvonta",
            allowed_features=["sequences", "not_a_real_feature"],
        )
        db.session.commit()

        assert result.organization.name == "Kutsu Oy"
        assert User.query.filter_by(organization_id=result.organization.id).count() == 0
        assert SearchProfile.query.filter_by(organization_id=result.organization.id).count() == 0
        assert result.invite.allowed_features == ["sequences"]  # unknown key dropped
        assert len(result.token) > 20

        audit = AuditLog.query.filter_by(action="customer_invited").first()
        assert audit is not None


def test_get_valid_invite_rejects_expired_revoked_or_accepted(app):
    with app.app_context():
        actor = create_user("actor-invite2@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        result = create_invite(
            actor=actor,
            organization_name="Vanheneva Oy",
            admin_email="admin@vanheneva.fi",
            search_profile_name="Profiili",
            remonttityyppi="Putkiremontti",
        )
        db.session.commit()

        assert get_valid_invite(result.token) is not None

        invite = CustomerInvite.query.get(result.invite.id)
        invite.revoked_at = invite.created_at
        db.session.commit()
        assert get_valid_invite(result.token) is None


@patch("app.email.services._mailgun_send")
def test_accept_invite_creates_admin_and_active_profile(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    with app.app_context():
        actor = create_user("actor-invite3@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        result = create_invite(
            actor=actor,
            organization_name="Hyväksy Oy",
            admin_email="admin@hyvaksy.fi",
            search_profile_name="Hyväksy profiili",
            remonttityyppi="Putkiremontti",
            allowed_features=["sequences", "automations"],
        )
        db.session.commit()

        accept_invite(
            result.invite,
            password="brandnewpassword1",
            regions=["Uusimaa", "Pirkanmaa"],
            enabled_features=["sequences", "ai_enrichment"],  # ai_enrichment was never allowed
        )
        db.session.commit()

        admin = User.query.filter_by(email="admin@hyvaksy.fi").one()
        assert admin.role == "admin"
        assert admin.organization_id == result.organization.id

        profile = SearchProfile.query.filter_by(organization_id=result.organization.id).one()
        assert profile.regions == ["Uusimaa", "Pirkanmaa"]
        # a profile is only a filter over the shared catalogue: it starts
        # right away, with nothing per-customer to schedule or key
        assert profile.is_active is True
        assert profile.needs_rematch is True
        assert profile.crm_api_key is None
        assert SearchJob.query.filter_by(search_profile_id=profile.id).count() == 0
        assert admin.must_change_password is False  # they chose the password themselves

        org = Organization.query.get(result.organization.id)
        assert org.enabled_features == ["sequences"]  # only what was allowed AND chosen

        refreshed_invite = CustomerInvite.query.get(result.invite.id)
        assert refreshed_invite.accepted_at is not None
        assert get_valid_invite(result.token) is None  # single-use

        mock_send.assert_called_once()
        assert mock_send.call_args.kwargs["to_email"] == "admin@hyvaksy.fi"


@patch("app.email.services._mailgun_send")
def test_accept_invite_stores_all_finland_as_its_own_value(mock_send, app):
    mock_send.return_value = (True, "<msg@test>", None)
    with app.app_context():
        actor = create_user("actor-invite4@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        result = create_invite(
            actor=actor,
            organization_name="Koko Suomi Oy",
            admin_email="admin@kokosuomi.fi",
            search_profile_name="Koko Suomi profiili",
            remonttityyppi="Putkiremontti",
        )
        db.session.commit()

        accept_invite(
            result.invite,
            password="brandnewpassword1",
            regions=[ALL_FINLAND_REGION],
            enabled_features=[],
        )
        db.session.commit()

        profile = SearchProfile.query.filter_by(organization_id=result.organization.id).one()
        assert profile.regions == ["Koko Suomi"]


@patch("app.email.services._mailgun_send")
def test_activate_source_rematches_instead_of_creating_a_job(mock_send, app):
    """Regression: an inactive profile has needs_rematch cleared by the
    scheduler, so activating it must set the flag again or the customer never
    gets leads from the catalogue's history."""
    from app.listings.services import rematch_profiles

    mock_send.return_value = (True, "<msg@test>", None)
    with app.app_context():
        actor = create_user("actor-invite5@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        result = create_invite(
            actor=actor,
            organization_name="Aktivoi Oy",
            admin_email="admin@aktivoi.fi",
            search_profile_name="Aktivoi profiili",
            remonttityyppi="Putkiremontti",
        )
        db.session.commit()
        accept_invite(result.invite, password="brandnewpassword1", regions=["Uusimaa"], enabled_features=[])
        db.session.commit()

        profile = SearchProfile.query.filter_by(organization_id=result.organization.id).one()
        with pytest.raises(InviteServiceError, match="aktiivinen"):
            activate_source(profile, actor=actor)
        db.session.rollback()

        # switched off -> the scheduler clears the rematch flag
        profile.is_active = False
        db.session.commit()
        rematch_profiles()
        assert SearchProfile.query.get(profile.id).needs_rematch is False

        activate_source(profile, actor=actor)
        db.session.commit()

        profile = SearchProfile.query.get(profile.id)
        assert profile.is_active is True
        assert profile.needs_rematch is True
        assert SearchJob.query.filter_by(search_profile_id=profile.id).count() == 0


def test_renew_invite_invalidates_previous_link(app):
    with app.app_context():
        actor = create_user("actor-invite6@test.com", "securepassword1", role="superadmin")
        db.session.commit()
        result = create_invite(
            actor=actor,
            organization_name="Uusinta Oy",
            admin_email="admin@uusinta.fi",
            search_profile_name="Profiili",
            remonttityyppi="Putkiremontti",
        )
        db.session.commit()
        old_token = result.token

        renewed = renew_invite(result.invite, actor=actor)
        db.session.commit()

        assert get_valid_invite(old_token) is None
        assert get_valid_invite(renewed.token) is not None
        assert renewed.invite.organization_id == result.organization.id


# --- Route-level: full invite -> accept -> activate flow, two isolated orgs ---


@patch("app.email.services.EmailService.send_customer_invite_email")
def test_full_invite_flow_two_isolated_organizations(mock_send, client, app):
    mock_send.return_value = {"success": True, "message_id": "<invite@test>", "error": None}
    _login_superadmin(client, app)

    def _invite(org_name, email, profile_name):
        client.post(
            "/admin/customers/invite",
            data={
                "organization_name": org_name,
                "admin_email": email,
                "search_profile_name": profile_name,
                "remonttityyppi": "Putkiremontti",
                "source": "oikotie",
                "allowed_features": ["sequences"],
            },
        )
        invite_url = mock_send.call_args.kwargs["invite_url"]
        return invite_url.rsplit("/", 1)[-1]

    token_a = _invite("Eka Oy", "admin@eka.fi", "Eka profiili")
    token_b = _invite("Toka Oy", "admin@toka.fi", "Toka profiili")

    # Both customers accept on a fresh (logged-out) client each.
    accept_client_a = app.test_client()
    resp_a = accept_client_a.post(
        f"/invite/{token_a}",
        data={
            "password": "ekasalasana1",
            "password_confirm": "ekasalasana1",
            "region_mode": "specific",
            "regions": ["Uusimaa"],
        },
        follow_redirects=True,
    )
    assert resp_a.status_code == 200

    accept_client_b = app.test_client()
    resp_b = accept_client_b.post(
        f"/invite/{token_b}",
        data={
            "password": "tokasalasana1",
            "password_confirm": "tokasalasana1",
            "region_mode": "all_finland",
        },
        follow_redirects=True,
    )
    assert resp_b.status_code == 200

    with app.app_context():
        org_a = Organization.query.filter_by(slug="eka-oy").one()
        org_b = Organization.query.filter_by(slug="toka-oy").one()
        profile_a = SearchProfile.query.filter_by(organization_id=org_a.id).one()
        profile_b = SearchProfile.query.filter_by(organization_id=org_b.id).one()
        assert profile_a.regions == ["Uusimaa"]
        assert profile_b.regions == [ALL_FINLAND_REGION]
        assert profile_a.is_active is True
        assert profile_b.is_active is True
        assert org_a.enabled_features == []  # allowed "sequences" but did not tick it

    # Each admin logs in and sees ONLY their own organization's search profile.
    # Each gets its own fresh app context: Flask caches the resolved current_user
    # on `g`, which is tied to the app context, not the request — reusing the
    # ambient context across different "logged in as" test clients within one
    # test would otherwise leak the previous identity into the next request.
    with app.app_context():
        login_a = app.test_client()
        login_a.post("/auth/login", data={"email": "admin@eka.fi", "password": "ekasalasana1"})
        page_a = login_a.get("/settings/search-profiles", follow_redirects=True).get_data(as_text=True)
        assert "Eka profiili" in page_a
        assert "Toka profiili" not in page_a

    with app.app_context():
        login_b = app.test_client()
        login_b.post("/auth/login", data={"email": "admin@toka.fi", "password": "tokasalasana1"})
        page_b = login_b.get("/settings/search-profiles", follow_redirects=True).get_data(as_text=True)
        assert "Toka profiili" in page_b
        assert "Eka profiili" not in page_b

    # No per-customer search job is ever created.
    with app.app_context():
        assert SearchJob.query.count() == 0

    # Re-visiting a now-accepted invite link must show it as no longer valid.
    stale = app.test_client().get(f"/invite/{token_a}")
    assert "Kutsu ei kelpaa".encode() in stale.data


def test_invite_route_requires_superadmin(client, app):
    with app.app_context():
        from app.users.services import create_organization

        org = create_organization("Admin Only Org", "admin-only-org")
        db.session.flush()
        create_user("admin-only@test.com", "securepassword1", role="admin", organization_id=org.id)
        db.session.commit()

    client.post("/auth/login", data={"email": "admin-only@test.com", "password": "securepassword1"})
    response = client.get("/admin/customers/invite")
    assert response.status_code == 403
