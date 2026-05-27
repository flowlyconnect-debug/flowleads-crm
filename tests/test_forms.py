from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.extensions import db
from app.forms.models import WebForm, WebFormSubmission
from app.forms.services import WebFormService, get_active_form_by_token
from app.forms.validators import validate_fields_config, validate_submission_payload
from app.leads.models import Lead
from app.sequences.models import EmailSequence
from app.users.services import create_organization, create_user


def _setup_org(app, slug="forms-org"):
    with app.app_context():
        org = create_organization(f"Org {slug}", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@example.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        user = create_user(
            f"user-{slug}@example.com",
            "securepassword1",
            role="user",
            organization_id=org.id,
        )
        db.session.commit()
        return org.id, admin.id, admin.email, user.id


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _default_fields():
    return [
        {"key": "first_name", "label": "Etunimi", "type": "text", "required": True},
        {"key": "email", "label": "Sähköposti", "type": "email", "required": True},
        {"key": "company", "label": "Yritys", "type": "text", "required": False},
    ]


def _create_form(org_id, user_id, *, is_active=True, notify_users=None, sequence_id=None):
    return WebFormService.create_form(
        {
            "name": "Contact",
            "title": "Ota yhteyttä",
            "fields": _default_fields(),
            "notify_users": notify_users or [],
            "auto_enroll_sequence_id": sequence_id,
            "is_active": is_active,
        },
        org_id,
        user_id,
    )


def test_form_creation_and_token(app):
    org_id, admin_id, _, _ = _setup_org(app, "create")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        assert form.id
        assert form.form_token
        assert len(form.form_token) >= 20


def test_validate_fields_config(app):
    ok, _ = validate_fields_config(_default_fields())
    assert ok is True
    ok, msg = validate_fields_config([{"key": "bad key!", "label": "X", "type": "text"}])
    assert ok is False
    assert msg


def test_public_form_definition(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "public-def")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    response = client.get(f"/api/public/forms/{token}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["data"]["title"] == "Ota yhteyttä"
    assert "organization_id" not in str(data)


def test_submission_creates_lead(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "submit-lead")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    payload = {"first_name": "Matti", "email": "matti@example.com", "company": "Acme"}
    with patch("app.forms.services._notify_users"), patch(
        "app.sequences.services.SequenceService.enroll_lead"
    ):
        response = client.post(
            f"/api/public/forms/{token}/submit",
            json=payload,
        )
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    with app.app_context():
        lead = Lead.query.filter_by(email="matti@example.com", organization_id=org_id).first()
        assert lead is not None
        assert lead.source == "webform"
        assert lead.company == "Acme"
        sub = WebFormSubmission.query.filter_by(form_id=form.id, status="processed").first()
        assert sub is not None
        assert sub.lead_id == lead.id


def test_duplicate_submission_rejected(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "dup")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    payload = {"first_name": "Matti", "email": "dup@example.com"}
    with patch("app.forms.services._notify_users"):
        client.post(f"/api/public/forms/{token}/submit", json=payload)
        response = client.post(f"/api/public/forms/{token}/submit", json=payload)
    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "duplicate_submission"


def test_required_field_validation(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "required")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    response = client.post(
        f"/api/public/forms/{token}/submit",
        json={"first_name": "Only"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"


def test_invalid_email_validation(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "email-val")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    response = client.post(
        f"/api/public/forms/{token}/submit",
        json={"first_name": "X", "email": "not-an-email"},
    )
    assert response.status_code == 400
    fields = response.get_json()["error"].get("fields")
    assert fields and "email" in fields


def test_select_option_validation(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "select")
    fields = _default_fields() + [
        {
            "key": "interest",
            "label": "Kiinnostus",
            "type": "select",
            "required": True,
            "options": ["A", "B"],
        }
    ]
    with app.app_context():
        form = WebFormService.create_form(
            {"name": "S", "title": "T", "fields": fields},
            org_id,
            admin_id,
        )
        db.session.commit()
        token = form.form_token
    response = client.post(
        f"/api/public/forms/{token}/submit",
        json={
            "first_name": "X",
            "email": "sel@example.com",
            "interest": "Invalid",
        },
    )
    assert response.status_code == 400


def test_rate_limit_enforced(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "rate")
    app.config["FORM_SUBMISSION_RATE_LIMIT"] = "2/hour"
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    payload = {"first_name": "R", "email": "rate1@example.com"}
    with patch("app.forms.services._notify_users"):
        for i in range(2):
            client.post(
                f"/api/public/forms/{token}/submit",
                json={**payload, "email": f"rate{i}@example.com"},
            )
        response = client.post(
            f"/api/public/forms/{token}/submit",
            json={**payload, "email": "rate3@example.com"},
        )
    assert response.status_code == 429


def test_inactive_form_404(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "inactive")
    with app.app_context():
        form = _create_form(org_id, admin_id, is_active=False)
        db.session.commit()
        token = form.form_token
    response = client.get(f"/api/public/forms/{token}")
    assert response.status_code == 404


def test_auto_enrollment_triggered(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "enroll")
    with app.app_context():
        seq = EmailSequence(
            organization_id=org_id,
            name="Welcome",
            is_active=True,
            trigger_type="manual",
        )
        db.session.add(seq)
        db.session.flush()
        form = _create_form(org_id, admin_id, sequence_id=seq.id)
        db.session.commit()
        token = form.form_token
    with patch("app.forms.services._notify_users"), patch(
        "app.sequences.services.SequenceService.enroll_lead"
    ) as enroll:
        client.post(
            f"/api/public/forms/{token}/submit",
            json={"first_name": "E", "email": "enroll@example.com"},
        )
        assert enroll.called


def test_notifications_sent(client, app):
    org_id, admin_id, _, user_id = _setup_org(app, "notify")
    with app.app_context():
        form = _create_form(org_id, admin_id, notify_users=[user_id])
        db.session.commit()
        token = form.form_token
    with patch("app.notifications.services.NotificationService.create") as notify:
        client.post(
            f"/api/public/forms/{token}/submit",
            json={"first_name": "N", "email": "notify@example.com"},
        )
        assert notify.called


def test_lead_upsert_by_email(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "upsert")
    with app.app_context():
        from app.leads.services import get_default_stage

        stage = get_default_stage(org_id)
        lead = Lead(
            organization_id=org_id,
            first_name="Old",
            email="upsert@example.com",
            source="manual",
            status="active",
            stage_id=stage.id,
        )
        db.session.add(lead)
        db.session.flush()
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    with patch("app.forms.services._notify_users"):
        client.post(
            f"/api/public/forms/{token}/submit",
            json={"first_name": "New", "email": "upsert@example.com", "company": "Updated Oy"},
        )
    with app.app_context():
        updated = Lead.query.filter_by(email="upsert@example.com").first()
        assert updated.first_name == "New"
        assert updated.company == "Updated Oy"


def test_cors_headers_present(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "cors")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    response = client.get(f"/api/public/forms/{token}")
    assert response.headers.get("Access-Control-Allow-Origin") == "*"


def test_embed_js_served(client, app):
    response = client.get("/static/forms/embed.js")
    assert response.status_code == 200
    assert b"data-form-token" in response.data


def test_iframe_embed_route(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "iframe")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    response = client.get(f"/forms/{token}/embed")
    assert response.status_code == 200
    assert b"fl-form" in response.data


def test_cross_tenant_isolation(client, app):
    org_a, admin_a, email_a, _ = _setup_org(app, "tenant-a")
    org_b, admin_b, _, _ = _setup_org(app, "tenant-b")
    with app.app_context():
        form_a = _create_form(org_a, admin_a)
        form_b = _create_form(org_b, admin_b)
        db.session.commit()
        form_id_b = form_b.id
    _login(client, email_a)
    response_other = client.get(f"/forms/{form_id_b}/submissions")
    assert response_other.status_code == 404


def test_soft_deleted_form_no_submissions(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "soft-del")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
        WebFormService.soft_delete_form(form.id, org_id)
        db.session.commit()
    response = client.post(
        f"/api/public/forms/{token}/submit",
        json={"first_name": "X", "email": "soft@example.com"},
    )
    assert response.status_code == 404


def test_malformed_payload_handled(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "malformed")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    response = client.post(
        f"/api/public/forms/{token}/submit",
        data="not json",
        content_type="text/plain",
    )
    assert response.status_code in (400, 200)
    body = response.get_json()
    assert "success" in body


def test_spam_honeypot(client, app):
    org_id, admin_id, _, _ = _setup_org(app, "spam")
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        token = form.form_token
    response = client.post(
        f"/api/public/forms/{token}/submit",
        json={"first_name": "Bot", "email": "bot@example.com", "_hp": "filled"},
    )
    assert response.get_json()["success"] is True
    with app.app_context():
        sub = WebFormSubmission.query.filter_by(form_id=form.id, status="spam").first()
        assert sub is not None
        assert Lead.query.filter_by(email="bot@example.com").first() is None


def test_admin_list_and_embed_code(client, app):
    org_id, admin_id, email, _ = _setup_org(app, "admin-ui")
    _login(client, email)
    with app.app_context():
        form = _create_form(org_id, admin_id)
        db.session.commit()
        form_id = form.id
    response = client.get("/forms")
    assert response.status_code == 200
    response = client.get(f"/forms/{form_id}/embed-code", headers={"Accept": "application/json"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["data"]["script"]
    assert data["data"]["iframe"]


def test_validate_submission_preserves_order(app):
    fields = [
        {"key": "a", "label": "A", "type": "text", "required": False},
        {"key": "b", "label": "B", "type": "text", "required": False},
    ]
    data, err = validate_submission_payload(fields, {"b": "2", "a": "1"})
    assert err is None
    assert list(data.keys()) == ["a", "b"]
