import json
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.search.models import SearchDedupe, SearchJob, SearchProfile
from app.users.services import create_organization


def _n8n_headers(secret: str = "test-n8n-secret"):
    return {"X-N8N-Secret": secret, "Content-Type": "application/json"}


def _create_profile_and_job(app, *, status="pending", scheduled_at=None, is_active=True):
    with app.app_context():
        org = create_organization("Search Org", "search-org")
        db.session.flush()
        profile = SearchProfile(
            organization_id=org.id,
            name="Test profile",
            remonttityyppi="Lukkoremontti / kulunvalvonta",
            regions=["Uusimaa", "Pirkanmaa"],
            source="oikotie",
            crm_api_key="fl_live_test",
            is_active=is_active,
        )
        db.session.add(profile)
        db.session.flush()
        when = scheduled_at or datetime.now(timezone.utc) - timedelta(minutes=5)
        job = SearchJob(
            search_profile_id=profile.id,
            organization_id=org.id,
            status=status,
            scheduled_at=when,
        )
        db.session.add(job)
        db.session.commit()
        return {
            "org_id": org.id,
            "profile_id": profile.id,
            "job_id": job.id,
        }


def test_list_jobs_requires_secret(client, app):
    _create_profile_and_job(app)
    response = client.get("/api/v1/n8n/jobs")
    assert response.status_code == 401


def test_list_pending_jobs(client, app):
    ctx = _create_profile_and_job(app)
    response = client.get(
        "/api/v1/n8n/jobs?status=pending&limit=10",
        headers=_n8n_headers(),
    )
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["jobs"]) == 1
    job = data["jobs"][0]
    assert job["job_id"] == ctx["job_id"]
    assert job["profile_id"] == ctx["profile_id"]
    assert job["organization_id"] == ctx["org_id"]
    assert job["remonttityyppi"] == "Lukkoremontti / kulunvalvonta"
    assert job["regions"] == ["Uusimaa", "Pirkanmaa"]
    assert job["source"] == "oikotie"
    # crm_api_key is now a short-lived job-scoped token, never the
    # organization's permanent API key.
    assert job["crm_api_key"] != "fl_live_test"
    assert len(job["crm_api_key"]) > 20
    assert job["crm_endpoint"] == "http://localhost/api/v1/leads"

    with app.app_context():
        from app.core.security import hash_api_key

        stored = db.session.get(SearchJob, ctx["job_id"])
        assert stored.n8n_token_hash == hash_api_key(job["crm_api_key"])
        assert stored.n8n_token_expires_at is not None


def test_list_pending_jobs_atomically_claims_them(client, app):
    """GET status=pending must mark the job 'running' as part of the same
    call — this is what prevents two overlapping n8n runs from both
    processing the same job (see claim_pending_jobs_for_n8n)."""
    ctx = _create_profile_and_job(app)

    response = client.get("/api/v1/n8n/jobs?status=pending&limit=10", headers=_n8n_headers())
    assert len(response.get_json()["jobs"]) == 1

    with app.app_context():
        job = db.session.get(SearchJob, ctx["job_id"])
        assert job.status == "running"
        assert job.started_at is not None

    # A second, concurrent poll for pending jobs must not see it again.
    second = client.get("/api/v1/n8n/jobs?status=pending&limit=10", headers=_n8n_headers())
    assert second.get_json()["jobs"] == []


def test_claim_pending_jobs_is_safe_against_concurrent_claims(app):
    """Two 'simultaneous' claim calls for the same job must never both win."""
    from app.search.services import claim_pending_jobs_for_n8n

    ctx = _create_profile_and_job(app)
    with app.app_context():
        first = claim_pending_jobs_for_n8n(limit=10)
        second = claim_pending_jobs_for_n8n(limit=10)
        assert [job.id for job, _token in first] == [ctx["job_id"]]
        first_token = first[0][1]
        assert len(first_token) > 20
        assert second == []


def test_job_token_authenticates_lead_creation(client, app):
    """The ephemeral job token n8n receives must actually work to post the
    lead it found — this is the real point of minting it."""
    ctx = _create_profile_and_job(app)
    jobs = client.get(
        "/api/v1/n8n/jobs?status=pending&limit=10", headers=_n8n_headers()
    ).get_json()["jobs"]
    token = jobs[0]["crm_api_key"]

    resp = client.post(
        "/api/v1/leads",
        data=json.dumps({"email": "lead@test.com", "source_ref": "oikotie-1"}),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 201
    created_lead_id = resp.get_json()["data"]["lead"]["id"]
    with app.app_context():
        from app.leads.models import Lead

        lead = db.session.get(Lead, created_lead_id)
        assert lead.organization_id == ctx["org_id"]


def test_job_token_stops_working_once_job_completes(client, app):
    ctx = _create_profile_and_job(app)
    jobs = client.get(
        "/api/v1/n8n/jobs?status=pending&limit=10", headers=_n8n_headers()
    ).get_json()["jobs"]
    token = jobs[0]["crm_api_key"]

    client.patch(
        f"/api/v1/n8n/jobs/{ctx['job_id']}",
        data=json.dumps({"status": "completed", "leads_found": 0, "leads_sent": 0}),
        headers=_n8n_headers(),
    )

    resp = client.post(
        "/api/v1/leads",
        data=json.dumps({"email": "late@test.com", "source_ref": "oikotie-2"}),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_expired_job_token_is_rejected(client, app):
    from datetime import timedelta

    ctx = _create_profile_and_job(app)
    jobs = client.get(
        "/api/v1/n8n/jobs?status=pending&limit=10", headers=_n8n_headers()
    ).get_json()["jobs"]
    token = jobs[0]["crm_api_key"]

    with app.app_context():
        job = db.session.get(SearchJob, ctx["job_id"])
        job.n8n_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()

    resp = client.post(
        "/api/v1/leads",
        data=json.dumps({"email": "expired@test.com", "source_ref": "oikotie-3"}),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_job_token_cannot_be_used_for_other_api_routes(client, app):
    """A leaked job token must only ever be able to post leads, not list/read
    anything else for the organization."""
    _create_profile_and_job(app)
    jobs = client.get(
        "/api/v1/n8n/jobs?status=pending&limit=10", headers=_n8n_headers()
    ).get_json()["jobs"]
    token = jobs[0]["crm_api_key"]

    resp = client.get("/api/v1/leads", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_non_pending_job_listing_never_includes_a_usable_token(client, app):
    ctx = _create_profile_and_job(app, status="completed")
    response = client.get(
        "/api/v1/n8n/jobs?status=completed&limit=10", headers=_n8n_headers()
    )
    jobs = response.get_json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == ctx["job_id"]
    assert jobs[0]["crm_api_key"] == ""


def test_list_jobs_skips_inactive_profiles(client, app):
    _create_profile_and_job(app, is_active=False)
    response = client.get("/api/v1/n8n/jobs", headers=_n8n_headers())
    assert response.status_code == 200
    assert response.get_json()["jobs"] == []


def test_patch_job_status(client, app):
    ctx = _create_profile_and_job(app)
    response = client.patch(
        f"/api/v1/n8n/jobs/{ctx['job_id']}",
        data=json.dumps(
            {
                "status": "running",
                "leads_found": 0,
                "leads_sent": 0,
                "error": "",
            }
        ),
        headers=_n8n_headers(),
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "running"

    with app.app_context():
        job = db.session.get(SearchJob, ctx["job_id"])
        assert job.status == "running"
        assert job.started_at is not None


def test_patch_job_completed_updates_profile_stats(client, app):
    ctx = _create_profile_and_job(app, status="running")
    response = client.patch(
        f"/api/v1/n8n/jobs/{ctx['job_id']}",
        data=json.dumps(
            {
                "status": "completed",
                "leads_found": 23,
                "leads_sent": 5,
                "error": "",
            }
        ),
        headers=_n8n_headers(),
    )
    assert response.status_code == 200

    with app.app_context():
        job = db.session.get(SearchJob, ctx["job_id"])
        profile = db.session.get(SearchProfile, ctx["profile_id"])
        assert job.leads_found == 23
        assert job.leads_sent == 5
        assert job.completed_at is not None
        assert profile.total_runs == 1
        assert profile.total_leads_sent == 5


def test_dedupe_check_and_mark(client, app):
    ctx = _create_profile_and_job(app)
    org_id = ctx["org_id"]

    check = client.post(
        "/api/v1/n8n/dedupe/check",
        data=json.dumps(
            {
                "organization_id": org_id,
                "source_ids": ["oikotie-123", "oikotie-456"],
            }
        ),
        headers=_n8n_headers(),
    )
    assert check.status_code == 200
    check_data = check.get_json()
    assert check_data["new_ids"] == ["oikotie-123", "oikotie-456"]
    assert check_data["existing_ids"] == []

    with app.app_context():
        db.session.add(
            SearchDedupe(organization_id=org_id, source_id="oikotie-456")
        )
        db.session.commit()

    check2 = client.post(
        "/api/v1/n8n/dedupe/check",
        data=json.dumps(
            {
                "organization_id": org_id,
                "source_ids": ["oikotie-123", "oikotie-456"],
            }
        ),
        headers=_n8n_headers(),
    )
    assert check2.get_json()["new_ids"] == ["oikotie-123"]
    assert check2.get_json()["existing_ids"] == ["oikotie-456"]

    mark = client.post(
        "/api/v1/n8n/dedupe/mark",
        data=json.dumps(
            {"organization_id": org_id, "source_ids": ["oikotie-123"]}
        ),
        headers=_n8n_headers(),
    )
    assert mark.status_code == 200
    assert mark.get_json()["marked"] == 1

    mark_again = client.post(
        "/api/v1/n8n/dedupe/mark",
        data=json.dumps(
            {"organization_id": org_id, "source_ids": ["oikotie-123"]}
        ),
        headers=_n8n_headers(),
    )
    assert mark_again.get_json()["marked"] == 0
