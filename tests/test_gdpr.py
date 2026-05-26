import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.api.services import create_api_key, patch_lead
from app.custom_fields.models import CustomFieldDefinition, CustomFieldValue
from app.custom_fields.services import CustomFieldService
from app.extensions import db
from app.gdpr.exports import DataExportService, DataExportServiceError
from app.gdpr.services import GDPRService, GDPRServiceError
from app.gdpr.settings import get_privacy_settings, update_privacy_settings
from app.leads.models import Lead
from app.leads.services import LeadService, get_default_stage
from app.sequences.models import EmailSequenceEnrollment
from app.sequences.services import SequenceService
from app.tasks.models import OrganizationSettings
from app.users.models import AuditLog
from app.users.services import create_organization, create_user


def _setup_org(app, slug="gdpr-org"):
    with app.app_context():
        org = create_organization(f"GDPR Org {slug}", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        other = create_organization(f"Other {slug}", f"{slug}-other")
        db.session.flush()
        create_user(
            f"admin-other-{slug}@test.com",
            "securepassword1",
            role="admin",
            organization_id=other.id,
        )
        db.session.commit()
        stage = get_default_stage(org.id)
        other_stage = get_default_stage(other.id)
        return {
            "org_id": org.id,
            "other_org_id": other.id,
            "admin_id": admin.id,
            "admin_email": admin.email,
            "stage_id": stage.id,
            "other_stage_id": other_stage.id,
        }


def _auth_headers(key: str):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _create_api_key(app, org_id):
    with app.app_context():
        _, full_key = create_api_key(org_id, "gdpr test", test_mode=True)
        db.session.commit()
        return full_key


def _create_lead(app, org_id, stage_id, **kwargs):
    with app.app_context():
        lead = LeadService.create(
            {
                "email": kwargs.get("email", "gdpr@example.com"),
                "first_name": kwargs.get("first_name", "Matti"),
                "last_name": kwargs.get("last_name", "Meikäläinen"),
                "phone": kwargs.get("phone", "+358401234567"),
                "linkedin_url": kwargs.get("linkedin_url", "https://linkedin.com/in/matti"),
                "notes": kwargs.get("notes", "Secret notes"),
                "stage_id": stage_id,
            },
            org_id,
            kwargs.get("user_id"),
            actor_role="admin",
        )
        db.session.commit()
        return lead.id


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_anonymization_replaces_personal_data(app):
    ctx = _setup_org(app)
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        GDPRService.anonymize_lead(lead_id, ctx["admin_id"], ctx["org_id"], reason="test")
        db.session.commit()
        lead = db.session.get(Lead, lead_id)
        assert lead.is_anonymized
        assert lead.email == f"anonymized_{lead_id}@deleted.invalid"
        assert lead.first_name == "Anonymoitu"
        assert lead.last_name == "Henkilö"
        assert lead.phone is None
        assert lead.linkedin_url is None
        assert lead.ai_summary is None
        assert "GDPR" in (lead.notes or "")
        assert lead.marketing_opt_in is False
        assert lead.gdpr_consent is False
        assert lead.unsubscribed is True
        assert lead.unsubscribed_at is not None


def test_anonymized_lead_remains_in_db(app):
    ctx = _setup_org(app)
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        GDPRService.anonymize_lead(lead_id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        assert db.session.get(Lead, lead_id) is not None


def test_anonymization_idempotent(app):
    ctx = _setup_org(app)
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        GDPRService.anonymize_lead(lead_id, ctx["admin_id"], ctx["org_id"])
        GDPRService.anonymize_lead(lead_id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        lead = db.session.get(Lead, lead_id)
        assert lead.is_anonymized


def test_anonymization_cancels_active_enrollments(app):
    ctx = _setup_org(app)
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        seq = SequenceService.create_sequence(
            {"name": "GDPR Seq", "trigger_type": "manual"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        SequenceService.add_step(
            seq.id,
            {
                "order_index": 0,
                "delay_days": 0,
                "subject_template": "Hi",
                "body_html_template": "<p>Hi</p>",
            },
            ctx["org_id"],
        )
        enrollment = SequenceService.enroll_lead(lead_id, seq.id, ctx["admin_id"], ctx["org_id"])
        assert enrollment.status == "active"
        GDPRService.anonymize_lead(lead_id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        en = db.session.get(EmailSequenceEnrollment, enrollment.id)
        assert en.status in ("cancelled", "unsubscribed")


def test_anonymization_deletes_custom_field_values(app):
    ctx = _setup_org(app)
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        defn = CustomFieldService.create_definition(
            ctx["org_id"],
            {"name": "tier", "label": "Tier", "field_type": "text"},
        )
        CustomFieldService.set_value(lead_id, "lead", defn.id, "gold", ctx["org_id"])
        db.session.commit()
        assert (
            CustomFieldValue.query.filter_by(
                entity_id=lead_id, entity_type="lead", organization_id=ctx["org_id"]
            ).count()
            == 1
        )
        GDPRService.anonymize_lead(lead_id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        assert (
            CustomFieldValue.query.filter_by(
                entity_id=lead_id, entity_type="lead", organization_id=ctx["org_id"]
            ).count()
            == 0
        )


def test_unsubscribed_lead_skipped_in_sequence_send(app):
    ctx = _setup_org(app)
    with app.app_context():
        lead = LeadService.create(
            {"email": "unsub@example.com", "stage_id": ctx["stage_id"]},
            ctx["org_id"],
            ctx["admin_id"],
        )
        lead.unsubscribed = True
        db.session.commit()
        seq = SequenceService.create_sequence(
            {"name": "Skip", "trigger_type": "manual"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        SequenceService.add_step(
            seq.id,
            {
                "order_index": 0,
                "delay_days": 0,
                "subject_template": "Hi",
                "body_html_template": "<p>Hi</p>",
            },
            ctx["org_id"],
        )
        with pytest.raises(Exception) as exc:
            SequenceService.enroll_lead(lead.id, seq.id, ctx["admin_id"], ctx["org_id"])
        assert getattr(exc.value, "code", None) == "unsubscribed"


def test_anonymized_lead_skipped_in_sequence_send(app):
    ctx = _setup_org(app)
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        GDPRService.anonymize_lead(lead_id, ctx["admin_id"], ctx["org_id"])
        db.session.commit()
        seq = SequenceService.create_sequence(
            {"name": "Skip Anon", "trigger_type": "manual"},
            ctx["org_id"],
            ctx["admin_id"],
        )
        SequenceService.add_step(
            seq.id,
            {
                "order_index": 0,
                "delay_days": 0,
                "subject_template": "Hi",
                "body_html_template": "<p>Hi</p>",
            },
            ctx["org_id"],
        )
        with pytest.raises(Exception) as exc:
            SequenceService.enroll_lead(lead_id, seq.id, ctx["admin_id"], ctx["org_id"])
        assert getattr(exc.value, "code", None) == "unsubscribed"


def test_data_export_includes_lead_records(app):
    ctx = _setup_org(app)
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        LeadService.log_activity(lead_id, ctx["admin_id"], "note", content="test note")
        db.session.commit()
        payload = DataExportService.export_lead(lead_id, ctx["org_id"])
        assert payload["lead"]["id"] == lead_id
        assert payload["lead"]["email"] == "gdpr@example.com"
        assert len(payload["activities"]) >= 1
        assert "audit_log" in payload


def test_api_can_set_gdpr_fields(app, client):
    ctx = _setup_org(app)
    key = _create_api_key(app, ctx["org_id"])
    response = client.post(
        "/api/v1/leads",
        data=json.dumps(
            {
                "email": "consent@example.com",
                "gdpr_consent": True,
                "gdpr_legal_basis": "consent",
                "marketing_opt_in": True,
            }
        ),
        headers=_auth_headers(key),
    )
    assert response.status_code == 201
    data = response.get_json()["data"]["lead"]
    assert data["gdpr_consent"] is True
    assert data["gdpr_legal_basis"] == "consent"
    assert data["marketing_opt_in"] is True


def test_consent_changes_create_audit_events(app, client):
    ctx = _setup_org(app)
    key = _create_api_key(app, ctx["org_id"])
    r1 = client.post(
        "/api/v1/leads",
        data=json.dumps({"email": "audit@example.com", "gdpr_consent": True}),
        headers=_auth_headers(key),
    )
    lead_id = r1.get_json()["data"]["lead"]["id"]
    with app.app_context():
        assert (
            AuditLog.query.filter_by(
                action="gdpr_consent_given", target_id=lead_id
            ).count()
            >= 1
        )
    client.patch(
        f"/api/v1/leads/{lead_id}",
        data=json.dumps({"gdpr_consent": False}),
        headers=_auth_headers(key),
    )
    with app.app_context():
        assert (
            AuditLog.query.filter_by(
                action="gdpr_consent_withdrawn", target_id=lead_id
            ).count()
            >= 1
        )


def test_retention_job_anonymizes_inactive_leads(app):
    ctx = _setup_org(app, "retention")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="old@example.com")
    with app.app_context():
        settings = get_privacy_settings(ctx["org_id"])
        settings.gdpr_auto_anonymize_inactive = True
        settings.gdpr_retention_days = 30
        lead = db.session.get(Lead, lead_id)
        old = datetime.now(timezone.utc) - timedelta(days=60)
        lead.updated_at = old
        lead.created_at = old
        lead.last_contacted_at = None
        db.session.commit()
        ids = GDPRService.run_retention_for_organization(ctx["org_id"])
        db.session.commit()
        assert lead_id in ids
        lead = db.session.get(Lead, lead_id)
        assert lead.is_anonymized
        assert (
            AuditLog.query.filter_by(
                action="gdpr_retention_anonymized", target_id=lead_id
            ).count()
            >= 1
        )


def test_export_request_creates_zip_and_expires_link(app):
    ctx = _setup_org(app, "export")
    with app.app_context():
        req = DataExportService.create_organization_export_request(
            ctx["org_id"], ctx["admin_id"]
        )
        db.session.commit()
        DataExportService._process_single_export(req)
        db.session.commit()
        assert req.status == "completed"
        assert req.file_path and os.path.isfile(req.file_path)
        exp = req.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        assert exp > datetime.now(timezone.utc)
        fetched = DataExportService.get_export_by_token(req.download_token)
        assert fetched.id == req.id


def test_export_token_expired(app):
    ctx = _setup_org(app, "expired")
    with app.app_context():
        req = DataExportService.create_organization_export_request(
            ctx["org_id"], ctx["admin_id"]
        )
        req.status = "completed"
        req.file_path = "/tmp/nonexistent.zip"
        req.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.session.commit()
        with pytest.raises(DataExportServiceError) as exc:
            DataExportService.get_export_by_token(req.download_token)
        assert exc.value.code == "expired"


def test_cross_tenant_anonymize_denied(app):
    ctx = _setup_org(app, "tenant")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        with pytest.raises(GDPRServiceError):
            GDPRService.anonymize_lead(lead_id, ctx["admin_id"], ctx["other_org_id"])


def test_cross_tenant_export_denied(app):
    ctx = _setup_org(app, "export-tenant")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    with app.app_context():
        with pytest.raises(DataExportServiceError):
            DataExportService.export_lead(lead_id, ctx["other_org_id"])


def test_gdpr_anonymize_route_requires_password(client, app):
    ctx = _setup_org(app, "route")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    _login(client, ctx["admin_email"])
    response = client.post(
        f"/leads/{lead_id}/gdpr/anonymize",
        data={"password": "wrong", "reason": "user request"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 200)
    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert not lead.is_anonymized


def test_gdpr_export_route_logs_audit(client, app):
    ctx = _setup_org(app, "export-route")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"])
    _login(client, ctx["admin_email"])
    response = client.get(f"/leads/{lead_id}/export")
    assert response.status_code == 200
    with app.app_context():
        assert (
            AuditLog.query.filter_by(
                action="gdpr_data_exported", target_id=lead_id
            ).count()
            >= 1
        )


def test_patch_lead_gdpr_via_service(app):
    ctx = _setup_org(app, "patch")
    lead_id = _create_lead(app, ctx["org_id"], ctx["stage_id"], email="patch@example.com")
    with app.app_context():
        patch_lead(ctx["org_id"], lead_id, {"gdpr_consent": True, "gdpr_legal_basis": "contract"})
        db.session.commit()
        lead = db.session.get(Lead, lead_id)
        assert lead.gdpr_consent is True
        assert lead.gdpr_legal_basis == "contract"
