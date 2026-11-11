"""Toimialat are the superadmin's decision: picked on the invite, stored on the
organization, never chosen by the customer. Imported (CSV) rows reach only the
customers entitled to the row's toimiala in the row's maakunta."""

import io
from unittest.mock import patch

from app.admin.invite_services import accept_invite, create_invite
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.listings.import_services import import_listings, import_match_summary
from app.listings.models import ListingMatch
from app.listings.services import ListingServiceError, rematch_profile
from app.listings.target_services import count_targets
from app.search.models import SearchProfile
from app.search.profile_services import (
    SearchProfileServiceError,
    create_profile,
    set_organization_types,
    update_profile,
)
from app.users.models import Organization
from app.users.services import create_user

import pytest

LUKITUS = "Lukitus, kulunvalvonta ja oviautomatiikka"
PUTKI = "Putki- ja viemäriurakointi"
KATTO = "Kattourakointi"
HISSI = "Hissihuolto ja hissien modernisointi"


def _superadmin(email="sa-toimiala@test.com"):
    user = create_user(email, "securepassword1", role="superadmin", organization_id=None)
    user.totp_enabled = True
    db.session.commit()
    return user


def _customer(name, types, regions=("Pirkanmaa",)):
    """An invited customer that has accepted: (organization, profile)."""
    actor = db.session.get(type(_customer.actor), _customer.actor.id)
    result = create_invite(
        actor=actor,
        organization_name=name,
        admin_email=f"admin@{name.lower().replace(' ', '')}.fi",
        search_profile_name=f"{name} haku",
        remonttityypit=types,
    )
    db.session.commit()
    with patch("app.email.services.EmailService.send_onboarding_complete_email"):
        accepted = accept_invite(
            result.invite, password="Hyv4-Salasana-123!", regions=list(regions), enabled_features=[]
        )
    accepted.search_profile.needs_rematch = False
    db.session.commit()
    return accepted.organization, accepted.search_profile


@pytest.fixture
def actor(app):
    with app.app_context():
        _customer.actor = _superadmin()
        yield _customer.actor


def _row(n, **overrides):
    row = {"taloyhtio": f"As Oy Testi {n}", "osoite": f"Testikatu {n}", "kaupunki": "Tampere", "maakunta": "Pirkanmaa"}
    row.update(overrides)
    return row


def _login(client, email):
    client.post("/auth/login", data={"email": email, "password": "securepassword1"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True


# --- invite -------------------------------------------------------------


def test_invite_fixes_the_customers_toimialat(actor):
    org, profile = _customer("Lukko Oy", [PUTKI, LUKITUS])
    # catalogue order, whatever order the form sent
    assert org.remonttityypit == [LUKITUS, PUTKI]
    assert profile.wanted_types == [LUKITUS, PUTKI]
    assert profile.remonttityyppi == LUKITUS


def test_invite_needs_a_toimiala(actor):
    from app.admin.invite_services import InviteServiceError

    with pytest.raises(InviteServiceError):
        create_invite(
            actor=actor, organization_name="Tyhjä Oy", admin_email="a@tyhja.fi", search_profile_name="x", remonttityypit=[]
        )
    db.session.rollback()
    assert Organization.query.filter_by(name="Tyhjä Oy").first() is None


@patch("app.email.services.EmailService.send_onboarding_complete_email")
@patch("app.email.services.EmailService.send_customer_invite_email")
def test_invite_page_has_no_toimiala_choice_and_ignores_a_posted_one(mock_send, _mock_done, client, app):
    mock_send.return_value = {"success": True, "message_id": "<i@test>", "error": None}
    with app.app_context():
        _superadmin("sa-page@test.com")
    _login(client, "sa-page@test.com")
    client.post(
        "/admin/customers/invite",
        data={
            "organization_name": "Katto Oy",
            "admin_email": "admin@katto.fi",
            "search_profile_name": "Katot",
            "remonttityypit": [KATTO, HISSI],
        },
    )
    token = mock_send.call_args.kwargs["invite_url"].rsplit("/", 1)[-1]
    client.get("/auth/logout")

    page = client.get(f"/invite/{token}").get_data(as_text=True)
    assert KATTO in page and HISSI in page
    assert 'name="remonttityyp' not in page

    client.post(
        f"/invite/{token}",
        data={
            "password": "Hyv4-Salasana-123!",
            "password_confirm": "Hyv4-Salasana-123!",
            "region_mode": "all_finland",
            "remonttityyppi": PUTKI,
            "remonttityypit": [PUTKI],
        },
    )
    with app.app_context():
        org = Organization.query.filter_by(name="Katto Oy").one()
        profile = SearchProfile.query.filter_by(organization_id=org.id).one()
        assert org.remonttityypit == [KATTO, HISSI]
        assert profile.wanted_types == [KATTO, HISSI]


# --- the customer cannot widen it ---------------------------------------


def test_customer_cannot_add_a_toimiala_in_settings(actor, client, app):
    org, profile = _customer("Hissi Oy", [HISSI])
    org_id, profile_id = org.id, profile.id
    client.post("/auth/login", data={"email": "admin@hissioy.fi", "password": "Hyv4-Salasana-123!"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True

    page = client.get("/settings/search-profiles").get_data(as_text=True)
    assert f'value="{HISSI}"' in page
    assert f'value="{PUTKI}"' not in page

    for data in (
        {"name": "Laajennettu", "remonttityypit": [HISSI, PUTKI], "region_mode": "all_finland", "is_active": "y"},
        {"name": "Vaihdettu", "remonttityyppi": PUTKI, "region_mode": "all_finland", "is_active": "y"},
    ):
        client.post(f"/settings/search-profiles/{profile_id}", data=data)
        client.post("/settings/search-profiles", data=data)
    with app.app_context():
        profiles = SearchProfile.query.filter_by(organization_id=org_id).all()
        assert [p.wanted_types for p in profiles] == [[HISSI]]
        assert profiles[0].name == "Hissi Oy haku"


def test_profile_services_refuse_types_outside_the_entitlement(actor):
    org, profile = _customer("Putki Oy", [PUTKI])
    with pytest.raises(SearchProfileServiceError):
        create_profile(org.id, name="Toinen", remonttityyppi=KATTO, regions=["Uusimaa"])
    db.session.rollback()
    with pytest.raises(SearchProfileServiceError):
        update_profile(
            profile, name="x", remonttityyppi=PUTKI, remonttityypit=[PUTKI, KATTO], regions=["Uusimaa"], is_active=True
        )
    db.session.rollback()

    empty = Organization(name="Ei toimialoja", slug="ei-toimialoja", remonttityypit=[])
    db.session.add(empty)
    db.session.flush()
    with pytest.raises(SearchProfileServiceError):
        create_profile(empty.id, name="x", remonttityyppi=PUTKI, regions=["Uusimaa"])


def test_stale_profile_types_never_match_outside_the_entitlement(actor):
    org, profile = _customer("Vanha Oy", [PUTKI, KATTO])
    # entitlement narrowed behind the profile's back (e.g. direct DB edit)
    org.remonttityypit = [PUTKI]
    db.session.commit()
    assert profile.wanted_types == [PUTKI]
    import_listings([_row(1, remonttityyppi=KATTO), _row(2, remonttityyppi=PUTKI)])
    assert count_targets(org.id) == 1


# --- the superadmin changes it ------------------------------------------


def test_superadmin_changes_toimialat_and_profiles_follow(actor, client, app):
    org, profile = _customer("Muuttuva Oy", [PUTKI, KATTO])
    org_id, profile_id = org.id, profile.id
    import_listings([_row(1, remonttityyppi=KATTO), _row(2, remonttityyppi=HISSI)])
    assert count_targets(org_id) == 1

    _login(client, "sa-toimiala@test.com")
    response = client.post(f"/admin/customers/{org_id}/toimialat", data={"remonttityypit": [PUTKI, HISSI]})
    assert response.status_code == 302
    with app.app_context():
        profile = db.session.get(SearchProfile, profile_id)
        assert db.session.get(Organization, org_id).remonttityypit == [PUTKI, HISSI]
        assert profile.wanted_types == [PUTKI, HISSI]
        assert profile.needs_rematch is True
        rematch_profile(profile)
        db.session.commit()
        matched = {
            m.listing.taloyhtio for m in ListingMatch.query.filter_by(profile_id=profile_id, status="pending").all()
        }
        assert matched == {"As Oy Testi 2"}

    # an empty selection is refused, nothing changes
    client.post(f"/admin/customers/{org_id}/toimialat", data={})
    with app.app_context():
        assert db.session.get(Organization, org_id).remonttityypit == [PUTKI, HISSI]


def test_changing_toimialat_keeps_what_the_customer_switched_off(actor):
    org, profile = _customer("Valikoiva Oy", [PUTKI, KATTO, HISSI])
    update_profile(profile, name="x", remonttityyppi=PUTKI, remonttityypit=[PUTKI], regions=["Pirkanmaa"], is_active=True)
    db.session.commit()
    set_organization_types(org, [PUTKI, KATTO, LUKITUS])
    db.session.commit()
    # KATTO stays off, HISSI is gone, the newly granted LUKITUS is on
    assert profile.wanted_types == [LUKITUS, PUTKI]


def test_customers_only_see_toimiala_change_route_as_superadmin(actor, client, app):
    org, _ = _customer("Utelias Oy", [PUTKI])
    org_id = org.id
    client.post("/auth/login", data={"email": "admin@uteliasoy.fi", "password": "Hyv4-Salasana-123!"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    response = client.post(f"/admin/customers/{org_id}/toimialat", data={"remonttityypit": [PUTKI, KATTO]})
    assert response.status_code in (302, 403)
    with app.app_context():
        assert db.session.get(Organization, org_id).remonttityypit == [PUTKI]


# --- CSV import reaches only the right customers -------------------------


def test_imported_rows_reach_only_customers_with_that_toimiala_and_region(actor):
    lukko, _ = _customer("Lukko A", [LUKITUS])
    putki, _ = _customer("Putki B", [PUTKI])
    both, _ = _customer("Molemmat C", [LUKITUS, PUTKI], regions=("Uusimaa",))

    result = import_listings(
        [
            _row(1, remonttityyppi=LUKITUS),
            _row(2, remonttityyppi=PUTKI),
            _row(3, remonttityyppi="Putkiremontti"),  # old name still understood
            _row(4, remonttityyppi=LUKITUS, maakunta="Uusimaa", kaupunki="Espoo"),
            _row(5, remonttityyppi=KATTO),
        ]
    )
    assert result["errors"] == []
    assert count_targets(lukko.id) == 1
    assert count_targets(putki.id) == 2
    assert count_targets(both.id) == 1

    summary = import_match_summary(result["analysed_listing_ids"])
    assert dict(summary["customers"]) == {"Lukko A": 1, "Putki B": 2, "Molemmat C": 1}
    assert summary["unmatched"] == 1  # the roof job: nobody has that toimiala


def test_whole_file_can_be_marked_at_upload(actor):
    katto, _ = _customer("Katto D", [KATTO])
    putki, _ = _customer("Putki E", [PUTKI])

    result = import_listings(
        [
            {"taloyhtio": "As Oy Yksi", "osoite": "Katu 1"},
            {"taloyhtio": "As Oy Kaksi", "osoite": "Katu 2", "remonttityyppi": PUTKI},  # the row's own value wins
            {"taloyhtio": "As Oy Kolme", "osoite": "Katu 3", "maakunta": "Lappi"},
        ],
        default_types=[KATTO],
        default_maakunta="pirkanmaa",
    )
    assert result["errors"] == [] and result["analysed"] == 3
    assert count_targets(katto.id) == 1
    assert count_targets(putki.id) == 1

    with pytest.raises(ListingServiceError):
        import_listings([_row(9)], default_types=["Ei ole"])
    with pytest.raises(ListingServiceError):
        import_listings([_row(9)], default_maakunta="Narnia")


def test_csv_upload_form_marks_the_file_and_reports_customers(actor, client, app):
    katto, _ = _customer("Katto F", [KATTO])
    katto_id = katto.id
    _login(client, "sa-toimiala@test.com")

    page = client.get("/admin/catalogue").get_data(as_text=True)
    assert 'name="default_remonttityypit"' in page and 'name="default_maakunta"' in page

    csv_bytes = "taloyhtio;osoite;kaupunki\r\nAs Oy Harja;Harjakatu 1;Tampere\r\nAs Oy Räystäs;Räystäskuja 2;Nokia\r\n".encode()
    response = client.post(
        "/admin/catalogue/import",
        data={
            "file": (io.BytesIO(csv_bytes), "katot.csv"),
            "default_remonttityypit": [KATTO],
            "default_maakunta": "Pirkanmaa",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert "Tuotu 2 uutta" in body
    assert "Katto F (2)" in body
    with app.app_context():
        assert count_targets(katto_id) == 2
