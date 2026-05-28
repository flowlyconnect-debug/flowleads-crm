import json
from datetime import datetime, timedelta, timezone

import pyotp
import pytest

from app.api.models import APIKey
from app.api.services import create_api_key, revoke_api_key
from app.core.permissions import TWO_FA_SESSION_KEY
from app.core.security import hash_api_key
from app.extensions import db
from app.leads.models import Activity, Lead, LeadStream, PipelineStage
from app.users.models import AuditLog
from app.users.services import create_organization, create_user


def _setup_org(app, slug="api-org"):
    with app.app_context():
        org = create_organization(f"Org {slug}", slug)
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
            "admin_id": admin.id,
            "admin_email": admin.email,
            "user_email": user.email,
            "other_admin_email": other_admin.email,
        }


def _create_api_key_for_org(app, org_id, name="test key"):
    with app.app_context():
        api_key, full_key = create_api_key(org_id, name, test_mode=True)
        db.session.commit()
        return full_key, api_key.id


def _auth_headers(key: str, use_bearer: bool = True):
    if use_bearer:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    return {"X-API-Key": key, "Content-Type": "application/json"}


def _post_lead(client, key, payload, **kwargs):
    return client.post(
        "/api/v1/leads",
        data=json.dumps(payload),
        headers=_auth_headers(key),
        **kwargs,
    )


# --- Health & auth ---


def test_health_without_key(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["status"] == "ok"
    assert data["data"]["version"] == "1.0.0"
    assert data["error"] is None


def test_valid_bearer_key_succeeds(client, app):
    ctx = _setup_org(app)
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    response = client.get("/api/v1/me", headers=_auth_headers(full_key))
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["organization"]["id"] == ctx["org_id"]


def test_valid_x_api_key_succeeds(client, app):
    ctx = _setup_org(app)
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    response = client.get(
        "/api/v1/me",
        headers=_auth_headers(full_key, use_bearer=False),
    )
    assert response.status_code == 200


def test_missing_key_returns_401(client):
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "missing_api_key"


def test_invalid_key_returns_401(client):
    response = client.get("/api/v1/me", headers=_auth_headers("fl_test_invalid"))
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_api_key"


def test_expired_key_returns_401(client, app):
    ctx = _setup_org(app, "expired")
    with app.app_context():
        api_key, full_key = create_api_key(ctx["org_id"], "expired", test_mode=True)
        api_key.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.session.commit()
    response = client.get("/api/v1/me", headers=_auth_headers(full_key))
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "expired_api_key"


def test_revoked_key_returns_401(client, app):
    ctx = _setup_org(app, "revoked")
    full_key, key_id = _create_api_key_for_org(app, ctx["org_id"])
    with app.app_context():
        revoke_api_key(key_id)
        db.session.commit()
    response = client.get("/api/v1/me", headers=_auth_headers(full_key))
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "inactive_api_key"


# --- Lead creation ---


def test_post_creates_lead(client, app):
    ctx = _setup_org(app, "create")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    response = _post_lead(
        client,
        full_key,
        {"email": "john@example.com", "first_name": "John"},
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["data"]["action"] == "created"
    assert data["data"]["lead"]["email"] == "john@example.com"

    with app.app_context():
        lead = Lead.query.filter_by(email="john@example.com").first()
        assert lead.organization_id == ctx["org_id"]
        stage = db.session.get(PipelineStage, lead.stage_id)
        assert stage.name == "New Lead"
        assert Activity.query.filter_by(lead_id=lead.id, type="created").count() == 1
        assert "API" in (Activity.query.filter_by(lead_id=lead.id).first().content or "")


# --- Upsert ---


def test_same_email_updates_lead(client, app):
    ctx = _setup_org(app, "upsert-email")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    _post_lead(client, full_key, {"email": "dup@example.com", "first_name": "A"})
    response = _post_lead(
        client,
        full_key,
        {"email": "dup@example.com", "last_name": "B"},
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["action"] == "updated"
    with app.app_context():
        lead = Lead.query.filter_by(email="dup@example.com").one()
        assert lead.first_name == "A"
        assert lead.last_name == "B"


def test_same_source_ref_updates(client, app):
    ctx = _setup_org(app, "upsert-ref")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    _post_lead(
        client,
        full_key,
        {
            "email": "a@example.com",
            "source": "n8n",
            "source_ref": "item-1",
            "company": "Acme",
        },
    )
    response = _post_lead(
        client,
        full_key,
        {
            "email": "b@example.com",
            "source": "n8n",
            "source_ref": "item-1",
            "company": "Acme Updated",
        },
    )
    assert response.get_json()["data"]["action"] == "updated"
    with app.app_context():
        assert Lead.query.filter_by(source_ref="item-1").count() == 1
        lead = Lead.query.filter_by(source_ref="item-1").one()
        assert lead.company == "Acme Updated"


def test_null_fields_do_not_overwrite(client, app):
    ctx = _setup_org(app, "null-fields")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    _post_lead(
        client,
        full_key,
        {"email": "keep@example.com", "first_name": "Keep", "company": "Co"},
    )
    _post_lead(
        client,
        full_key,
        {"email": "keep@example.com", "first_name": None, "company": None},
    )
    with app.app_context():
        lead = Lead.query.filter_by(email="keep@example.com").one()
        assert lead.first_name == "Keep"
        assert lead.company == "Co"


def test_tags_merged_without_duplicates(client, app):
    ctx = _setup_org(app, "tags")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    _post_lead(client, full_key, {"email": "t@example.com", "tags": ["a", "b"]})
    _post_lead(client, full_key, {"email": "t@example.com", "tags": ["b", "c"]})
    with app.app_context():
        lead = Lead.query.filter_by(email="t@example.com").one()
        assert lead.tags == ["a", "b", "c"]


# --- Bulk ---


def test_bulk_accepts_up_to_100(client, app):
    ctx = _setup_org(app, "bulk-ok")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    leads = [{"email": f"bulk{i}@example.com"} for i in range(100)]
    response = client.post(
        "/api/v1/leads/bulk",
        data=json.dumps(leads),
        headers=_auth_headers(full_key),
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["created"] == 100


def test_bulk_rejects_more_than_100(client, app):
    ctx = _setup_org(app, "bulk-big")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    leads = [{"email": f"x{i}@example.com"} for i in range(101)]
    response = client.post(
        "/api/v1/leads/bulk",
        data=json.dumps(leads),
        headers=_auth_headers(full_key),
    )
    assert response.status_code == 400


def test_bulk_partial_errors(client, app):
    ctx = _setup_org(app, "bulk-partial")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    payload = [
        {"email": "good@example.com"},
        {"email": "not-an-email"},
    ]
    response = client.post(
        "/api/v1/leads/bulk",
        data=json.dumps(payload),
        headers=_auth_headers(full_key),
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["created"] == 1
    assert len(data["errors"]) == 1


def test_bulk_duplicate_emails_in_payload(client, app):
    ctx = _setup_org(app, "bulk-dup")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    payload = [
        {"email": "same@example.com", "first_name": "One"},
        {"email": "same@example.com", "first_name": "Two"},
    ]
    response = client.post(
        "/api/v1/leads/bulk",
        data=json.dumps(payload),
        headers=_auth_headers(full_key),
    )
    data = response.get_json()["data"]
    assert data["created"] == 1
    assert any(e["message"] == "Duplicate email in bulk payload." for e in data["errors"])


# --- List / detail / patch ---


def test_list_only_own_org_leads(client, app):
    ctx = _setup_org(app, "list")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    other_key, _ = _create_api_key_for_org(app, ctx["other_org_id"])
    _post_lead(client, full_key, {"email": "mine@example.com"})
    _post_lead(client, other_key, {"email": "theirs@example.com"})

    response = client.get("/api/v1/leads", headers=_auth_headers(full_key))
    emails = [item["email"] for item in response.get_json()["data"]["leads"]]
    assert "mine@example.com" in emails
    assert "theirs@example.com" not in emails


def test_stream_cross_tenant(client, app):
    ctx = _setup_org(app, "streams-cross")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    other_key, _ = _create_api_key_for_org(app, ctx["other_org_id"])
    with app.app_context():
        mine = LeadStream(
            organization_id=ctx["org_id"],
            name="Mine",
            source_match="linkedin",
            is_active=True,
            lead_count=2,
        )
        other = LeadStream(
            organization_id=ctx["other_org_id"],
            name="Other",
            source_match="website",
            is_active=True,
            lead_count=5,
        )
        db.session.add_all([mine, other])
        db.session.commit()

    mine_resp = client.get("/api/v1/streams", headers=_auth_headers(full_key))
    other_resp = client.get("/api/v1/streams", headers=_auth_headers(other_key))
    mine_names = {s["name"] for s in mine_resp.get_json()["data"]["streams"]}
    other_names = {s["name"] for s in other_resp.get_json()["data"]["streams"]}
    assert "Mine" in mine_names
    assert "Other" not in mine_names
    assert "Other" in other_names


def test_detail_cross_tenant_404(client, app):
    ctx = _setup_org(app, "cross")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    other_key, _ = _create_api_key_for_org(app, ctx["other_org_id"])
    r = _post_lead(client, other_key, {"email": "secret@example.com"})
    other_lead_id = r.get_json()["data"]["lead"]["id"]

    response = client.get(
        f"/api/v1/leads/{other_lead_id}",
        headers=_auth_headers(full_key),
    )
    assert response.status_code == 404


def test_patch_updates_allowed_fields(client, app):
    ctx = _setup_org(app, "patch")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    r = _post_lead(client, full_key, {"email": "patch@example.com", "first_name": "Old"})
    lead_id = r.get_json()["data"]["lead"]["id"]

    response = client.patch(
        f"/api/v1/leads/{lead_id}",
        data=json.dumps({"first_name": "New", "score": 80}),
        headers=_auth_headers(full_key),
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["lead"]["first_name"] == "New"
    assert response.get_json()["data"]["lead"]["score"] == 80


def test_patch_rejects_forbidden_fields(client, app):
    ctx = _setup_org(app, "patch-forbid")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    r = _post_lead(client, full_key, {"email": "pf@example.com"})
    lead_id = r.get_json()["data"]["lead"]["id"]

    response = client.patch(
        f"/api/v1/leads/{lead_id}",
        data=json.dumps({"organization_id": 999, "status": "won"}),
        headers=_auth_headers(full_key),
    )
    assert response.status_code == 400
    assert "not allowed" in response.get_json()["error"]["message"].lower()


# --- Rate limit ---


def test_rate_limit_returns_429(client, app):
    ctx = _setup_org(app, "rate")
    app.config["API_RATE_LIMIT"] = "2/minute"
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    headers = _auth_headers(full_key)
    for _ in range(3):
        response = client.get("/api/v1/me", headers=headers)
    assert response.status_code == 429
    assert response.get_json()["error"]["code"] == "rate_limit_exceeded"
    assert response.headers.get("Retry-After") is not None


# --- API key UI ---


def test_admin_can_create_own_org_key(client, app):
    ctx = _setup_org(app, "ui-admin")
    client.post(
        "/auth/login",
        data={"email": ctx["admin_email"], "password": "securepassword1"},
    )
    response = client.post(
        "/settings/api-keys",
        data={"name": "n8n prod"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"fl_test_" in response.data or b"Copy this API key" in response.data
    with app.app_context():
        assert AuditLog.query.filter_by(action="api_key_created").count() >= 1


def test_normal_user_cannot_create_key(client, app):
    ctx = _setup_org(app, "ui-user")
    client.post(
        "/auth/login",
        data={"email": ctx["user_email"], "password": "securepassword1"},
    )
    response = client.get("/settings/api-keys")
    assert response.status_code == 403


def test_superadmin_create_key_after_2fa(client, app):
    with app.app_context():
        sa = create_user("sa-api@test.com", "securepassword1", role="superadmin")
        sa.totp_enabled = True
        sa.totp_secret = pyotp.random_base32()
        org = create_organization("SA Org", "sa-org")
        db.session.commit()
        org_id = org.id

    client.post(
        "/auth/login",
        data={"email": "sa-api@test.com", "password": "securepassword1"},
    )
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True

    response = client.post(
        "/admin/api-keys",
        data={
            "name": "cross org",
            "organization_id": org_id,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Copy this API key" in response.data


def test_full_key_shown_only_once(client, app):
    ctx = _setup_org(app, "once")
    client.post(
        "/auth/login",
        data={"email": ctx["admin_email"], "password": "securepassword1"},
    )
    first = client.post(
        "/settings/api-keys",
        data={"name": "once"},
        follow_redirects=True,
    )
    assert b"fl_test_" in first.data
    second = client.get("/settings/api-keys")
    assert b"Copy this API key now" not in second.data


def test_me_never_returns_full_key_or_hash(client, app):
    ctx = _setup_org(app, "safe-me")
    full_key, _ = _create_api_key_for_org(app, ctx["org_id"])
    response = client.get("/api/v1/me", headers=_auth_headers(full_key))
    body = response.get_data(as_text=True)
    assert full_key not in body
    assert hash_api_key(full_key) not in body

