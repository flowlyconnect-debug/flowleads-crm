from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.search.models import SearchJob, SearchProfile
from app.users.services import create_organization, create_user


def _setup_org(app, slug="search-ui"):
    with app.app_context():
        org = create_organization(f"Search UI {slug}", slug)
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
            "admin_email": admin.email,
            "user_email": user.email,
            "other_admin_email": other_admin.email,
        }


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _profile_form(**overrides):
    data = {
        "name": "Lukkoremontti Uusimaa",
        "remonttityyppi": "Lukkoremontti / kulunvalvonta",
        "regions": ["Uusimaa", "Pirkanmaa"],
        "schedule_description": "daily",
        "crm_api_key": "fl_live_test_key",
        "is_active": "y",
    }
    data.update(overrides)
    return data


def test_search_profiles_requires_admin(app, client):
    ctx = _setup_org(app, "auth")
    _login(client, ctx["user_email"])
    response = client.get("/settings/search-profiles")
    assert response.status_code == 403


def test_search_profiles_list_and_create(app, client):
    ctx = _setup_org(app, "create")
    _login(client, ctx["admin_email"])

    response = client.get("/settings/search-profiles")
    assert response.status_code == 200
    assert "Hakuprofiilit" in response.get_data(as_text=True)

    create_resp = client.post(
        "/settings/search-profiles",
        data=_profile_form(),
        follow_redirects=False,
    )
    assert create_resp.status_code == 302

    with app.app_context():
        profile = SearchProfile.query.filter_by(organization_id=ctx["org_id"]).one()
        profile_id = profile.id
        assert profile.name == "Lukkoremontti Uusimaa"
        assert profile.regions == ["Uusimaa", "Pirkanmaa"]
        assert profile.schedule_description == "daily"
        assert profile.is_active is True

    detail = client.get(f"/settings/search-profiles?id={profile_id}")
    assert detail.status_code == 200
    assert "Viimeisin ajo" in detail.get_data(as_text=True)


def test_search_profiles_update(app, client):
    ctx = _setup_org(app, "update")
    _login(client, ctx["admin_email"])
    client.post("/settings/search-profiles", data=_profile_form())

    with app.app_context():
        profile = SearchProfile.query.filter_by(organization_id=ctx["org_id"]).one()
        profile_id = profile.id

    update_resp = client.post(
        f"/settings/search-profiles/{profile_id}",
        data=_profile_form(
            name="Putki Pirkanmaa",
            remonttityyppi="Putkiremontti",
            regions=["Pirkanmaa"],
            schedule_description="weekly",
        ),
        follow_redirects=False,
    )
    assert update_resp.status_code == 302

    with app.app_context():
        updated = db.session.get(SearchProfile, profile_id)
        assert updated.name == "Putki Pirkanmaa"
        assert updated.remonttityyppi == "Putkiremontti"
        assert updated.schedule_description == "weekly"


def test_search_profiles_tenant_isolation(app, client):
    ctx = _setup_org(app, "iso")
    _login(client, ctx["admin_email"])
    client.post("/settings/search-profiles", data=_profile_form())

    with app.app_context():
        profile = SearchProfile.query.filter_by(organization_id=ctx["org_id"]).one()
        profile_id = profile.id

    client.get("/auth/logout")
    _login(client, ctx["other_admin_email"])
    response = client.post(
        f"/settings/search-profiles/{profile_id}",
        data=_profile_form(name="Hacked"),
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_create_test_search_job_requires_admin(app, client):
    ctx = _setup_org(app, "testjob-auth")
    with app.app_context():
        profile = SearchProfile(
            organization_id=ctx["org_id"],
            name="Active profile",
            remonttityyppi="Putkiremontti",
            regions=["Uusimaa"],
            is_active=True,
        )
        db.session.add(profile)
        db.session.commit()
        profile_id = profile.id

    _login(client, ctx["user_email"])
    response = client.post(f"/settings/search-profiles/{profile_id}/create-test-job")
    assert response.status_code == 403


def test_create_test_search_job_success(app, client):
    ctx = _setup_org(app, "testjob-ok")
    with app.app_context():
        profile = SearchProfile(
            organization_id=ctx["org_id"],
            name="Active profile",
            remonttityyppi="Putkiremontti",
            regions=["Uusimaa"],
            is_active=True,
        )
        db.session.add(profile)
        db.session.commit()
        profile_id = profile.id

    _login(client, ctx["admin_email"])
    response = client.post(f"/settings/search-profiles/{profile_id}/create-test-job")
    assert response.status_code == 200
    data = response.get_json()
    assert data == {
        "success": True,
        "job_id": data["job_id"],
        "profile_id": profile_id,
        "status": "pending",
    }

    with app.app_context():
        job = SearchJob.query.filter_by(search_profile_id=profile_id).one()
        assert job.id == data["job_id"]
        assert job.status == "pending"


def test_create_test_search_job_rejects_duplicate(app, client):
    ctx = _setup_org(app, "testjob-dup")
    with app.app_context():
        profile = SearchProfile(
            organization_id=ctx["org_id"],
            name="Active profile",
            remonttityyppi="Putkiremontti",
            regions=["Uusimaa"],
            is_active=True,
        )
        db.session.add(profile)
        db.session.flush()
        db.session.add(
            SearchJob(
                search_profile_id=profile.id,
                organization_id=ctx["org_id"],
                status="pending",
                scheduled_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        profile_id = profile.id

    _login(client, ctx["admin_email"])
    response = client.post(f"/settings/search-profiles/{profile_id}/create-test-job")
    assert response.status_code == 409
    assert response.get_json()["success"] is False


def test_create_test_search_job_visible_in_n8n_pending_list(app, client):
    ctx = _setup_org(app, "testjob-n8n")
    with app.app_context():
        profile = SearchProfile(
            organization_id=ctx["org_id"],
            name="Active profile",
            remonttityyppi="Putkiremontti",
            regions=["Uusimaa"],
            is_active=True,
            crm_api_key="fl_live_test",
        )
        db.session.add(profile)
        db.session.commit()
        profile_id = profile.id

    _login(client, ctx["admin_email"])
    create_resp = client.post(f"/settings/search-profiles/{profile_id}/create-test-job")
    job_id = create_resp.get_json()["job_id"]

    pending = client.get(
        "/api/v1/n8n/jobs?status=pending&limit=10",
        headers={"X-N8N-Secret": "test-n8n-secret"},
    )
    assert pending.status_code == 200
    jobs = pending.get_json()["jobs"]
    assert any(job["job_id"] == job_id for job in jobs)


def test_search_profiles_sidepanel_shows_latest_job(app, client):
    ctx = _setup_org(app, "job")
    with app.app_context():
        profile = SearchProfile(
            organization_id=ctx["org_id"],
            name="Stats profile",
            remonttityyppi="Putkiremontti",
            regions=["Uusimaa"],
            total_runs=2,
            total_leads_sent=12,
            last_run_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.session.add(profile)
        db.session.flush()
        db.session.add(
            SearchJob(
                search_profile_id=profile.id,
                organization_id=ctx["org_id"],
                status="completed",
                scheduled_at=datetime.now(timezone.utc) - timedelta(hours=2),
                completed_at=datetime.now(timezone.utc) - timedelta(hours=1),
                leads_found=20,
                leads_sent=7,
            )
        )
        db.session.commit()
        profile_id = profile.id

    _login(client, ctx["admin_email"])
    response = client.get(f"/settings/search-profiles?id={profile_id}")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "7 (viimeisin ajo)" in text
    assert "Valmis" in text
