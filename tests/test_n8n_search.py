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
    assert job["crm_api_key"] == "fl_live_test"
    assert job["crm_endpoint"] == "http://localhost/api/v1/leads"


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
