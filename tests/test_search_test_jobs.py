from datetime import datetime, timezone

from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.search.job_scheduler import create_missing_test_jobs, reset_running_test_jobs
from app.search.models import SearchJob, SearchProfile
from app.users.services import create_organization, create_user


def _create_active_profile(app, *, slug_suffix="", is_active=True):
    with app.app_context():
        suffix = slug_suffix or str(datetime.now(timezone.utc).timestamp()).replace(".", "")
        org = create_organization("Test Jobs Org", f"test-jobs-org-{suffix}")
        db.session.flush()
        profile = SearchProfile(
            organization_id=org.id,
            name="Active profile",
            remonttityyppi="Putkiremontti",
            regions=["Uusimaa"],
            source="oikotie",
            schedule_description="daily",
            is_active=is_active,
        )
        db.session.add(profile)
        db.session.commit()
        return {"org_id": org.id, "profile_id": profile.id}


def _login_superadmin(client, app, email="sa-test-jobs@test.com"):
    with app.app_context():
        user = create_user(email, "securepassword1", role="superadmin")
        user.totp_enabled = True
        db.session.commit()
    client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
    )
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True


def test_create_missing_test_jobs_skips_when_pending_exists(app):
    ctx = _create_active_profile(app, slug_suffix="pending")
    now = datetime.now(timezone.utc)

    with app.app_context():
        db.session.add(
            SearchJob(
                search_profile_id=ctx["profile_id"],
                organization_id=ctx["org_id"],
                status="pending",
                scheduled_at=now,
            )
        )
        db.session.commit()

        result = create_missing_test_jobs(now=now)

        assert result == {"created": 0, "skipped": 1}
        assert SearchJob.query.filter_by(search_profile_id=ctx["profile_id"]).count() == 1


def test_create_missing_test_jobs_skips_when_running_exists(app):
    ctx = _create_active_profile(app, slug_suffix="running")
    now = datetime.now(timezone.utc)

    with app.app_context():
        db.session.add(
            SearchJob(
                search_profile_id=ctx["profile_id"],
                organization_id=ctx["org_id"],
                status="running",
                scheduled_at=now,
            )
        )
        db.session.commit()

        result = create_missing_test_jobs(now=now)

        assert result == {"created": 0, "skipped": 1}
        assert SearchJob.query.filter_by(search_profile_id=ctx["profile_id"]).count() == 1


def test_create_missing_test_jobs_creates_pending_job(app):
    ctx = _create_active_profile(app, slug_suffix="create")
    now = datetime.now(timezone.utc)

    with app.app_context():
        result = create_missing_test_jobs(now=now)

        assert result == {"created": 1, "skipped": 0}
        job = SearchJob.query.filter_by(search_profile_id=ctx["profile_id"]).one()
        assert job.status == "pending"
        assert job.organization_id == ctx["org_id"]
        assert job.scheduled_at.replace(tzinfo=timezone.utc) == now


def test_create_missing_test_jobs_ignores_inactive_profiles(app):
    _create_active_profile(app, slug_suffix="inactive", is_active=False)
    now = datetime.now(timezone.utc)

    with app.app_context():
        result = create_missing_test_jobs(now=now)

        assert result == {"created": 0, "skipped": 0}
        assert SearchJob.query.count() == 0


def test_create_test_jobs_endpoint_disabled_without_env(app, client):
    _create_active_profile(app, slug_suffix="endpoint-off")
    _login_superadmin(client, app)

    app.config["ENABLE_TEST_JOBS"] = False
    response = client.post("/admin/dev/create-test-jobs")

    assert response.status_code == 403


def test_create_test_jobs_endpoint_creates_jobs_when_enabled(app, client):
    ctx = _create_active_profile(app, slug_suffix="endpoint-on")
    _login_superadmin(client, app, email="sa-test-jobs-enabled@test.com")

    app.config["ENABLE_TEST_JOBS"] = True
    response = client.post("/admin/dev/create-test-jobs")

    assert response.status_code == 200
    assert response.get_json() == {"created": 1, "skipped": 0}

    with app.app_context():
        job = SearchJob.query.filter_by(search_profile_id=ctx["profile_id"]).one()
        assert job.status == "pending"


def _add_job(app, *, profile_id, org_id, status, scheduled_at=None):
    when = scheduled_at or datetime.now(timezone.utc)
    with app.app_context():
        job = SearchJob(
            search_profile_id=profile_id,
            organization_id=org_id,
            status=status,
            scheduled_at=when,
        )
        db.session.add(job)
        db.session.commit()
        return job.id


def test_reset_running_test_jobs_marks_running_as_failed(app):
    ctx = _create_active_profile(app, slug_suffix="reset-running")
    job_id = _add_job(
        app,
        profile_id=ctx["profile_id"],
        org_id=ctx["org_id"],
        status="running",
    )

    with app.app_context():
        result = reset_running_test_jobs()

        assert result == {"reset": 1}
        job = db.session.get(SearchJob, job_id)
        assert job.status == "failed"
        assert job.error_message == "Reset from running during n8n dev testing"


def test_reset_running_test_jobs_leaves_pending_untouched(app):
    ctx = _create_active_profile(app, slug_suffix="reset-pending")
    job_id = _add_job(
        app,
        profile_id=ctx["profile_id"],
        org_id=ctx["org_id"],
        status="pending",
    )

    with app.app_context():
        result = reset_running_test_jobs()

        assert result == {"reset": 0}
        job = db.session.get(SearchJob, job_id)
        assert job.status == "pending"
        assert job.error_message is None


def test_reset_running_test_jobs_leaves_completed_untouched(app):
    ctx = _create_active_profile(app, slug_suffix="reset-completed")
    job_id = _add_job(
        app,
        profile_id=ctx["profile_id"],
        org_id=ctx["org_id"],
        status="completed",
    )

    with app.app_context():
        result = reset_running_test_jobs()

        assert result == {"reset": 0}
        job = db.session.get(SearchJob, job_id)
        assert job.status == "completed"
        assert job.error_message is None


def test_reset_running_test_jobs_endpoint_disabled_without_env(app, client):
    _create_active_profile(app, slug_suffix="reset-endpoint-off")
    _login_superadmin(client, app, email="sa-reset-off@test.com")

    app.config["ENABLE_TEST_JOBS"] = False
    response = client.post("/admin/dev/reset-running-test-jobs")

    assert response.status_code == 403
