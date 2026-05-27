from __future__ import annotations

from datetime import timedelta, timezone, datetime
from unittest.mock import Mock, patch

from app.extensions import db
from app.users.services import create_organization, create_user
from app.webhooks.encryption import decrypt_webhook_secret, encrypt_webhook_secret
from app.webhooks.models import WebhookDelivery, WebhookEndpoint
from app.webhooks.services import WebhookService


def _setup_org(app, slug="webhook-org"):
    with app.app_context():
        org = create_organization(f"Org {slug}", slug)
        db.session.flush()
        admin = create_user(
            f"admin-{slug}@example.com",
            "securepassword1",
            role="admin",
            organization_id=org.id,
        )
        db.session.commit()
        return org.id, admin.id, admin.email


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _create_endpoint(organization_id, provider="custom", *, is_active=True, events=None):
    endpoint = WebhookEndpoint(
        organization_id=organization_id,
        name=f"{provider} endpoint",
        provider=provider,
        url_encrypted=encrypt_webhook_secret("https://example.test/webhook"),
        secret_encrypted=encrypt_webhook_secret("secret-value"),
        is_active=is_active,
        events=events or ["lead.created"],
    )
    db.session.add(endpoint)
    db.session.flush()
    return endpoint


def _response(status=200, text="ok"):
    res = Mock()
    res.status_code = status
    res.text = text
    return res


def test_webhook_url_encryption_decryption(app):
    with app.app_context():
        plain = "https://hooks.slack.test/services/abc"
        encrypted = encrypt_webhook_secret(plain)
        assert encrypted != plain
        assert decrypt_webhook_secret(encrypted) == plain


def test_webhook_secret_encryption_decryption(app):
    with app.app_context():
        encrypted = encrypt_webhook_secret("my-secret")
        assert encrypted != "my-secret"
        assert decrypt_webhook_secret(encrypted) == "my-secret"


def test_endpoint_does_not_store_plain_url(app):
    org_id, _, _ = _setup_org(app, "plain-url")
    with app.app_context():
        endpoint = _create_endpoint(org_id)
        db.session.commit()
        assert "https://example.test/webhook" not in endpoint.url_encrypted


def test_webhook_delivery_on_lead_created_event(app):
    org_id, _, _ = _setup_org(app, "lead-created")
    with app.app_context():
        _create_endpoint(org_id, events=["lead.created"])
        with patch("app.webhooks.services.requests.post", return_value=_response(200, "ok")):
            result = WebhookService.dispatch("lead.created", {"lead": {"id": 1}}, org_id)
            db.session.commit()
        assert result["matched_endpoints"] == 1
        assert result["delivered"] == 1


def test_slack_payload_format(app):
    with app.app_context():
        payload = WebhookService.format_payload(
            "lead.created",
            {"data": {"lead": {"company": "Acme", "name": "John", "score": 82, "source": "n8n", "crm_url": "https://app.flowleads.fi/leads/1"}}},
            "slack",
        )
        assert "blocks" in payload
        assert payload["blocks"][0]["type"] == "section"


def test_teams_payload_format(app):
    with app.app_context():
        payload = WebhookService.format_payload(
            "lead.created",
            {"data": {"lead": {"name": "John", "crm_url": "https://app.flowleads.fi/leads/1"}}},
            "teams",
        )
        assert payload["type"] == "message"
        assert payload["attachments"][0]["content"]["type"] == "AdaptiveCard"


def test_custom_payload_envelope_format(app):
    with app.app_context():
        payload = WebhookService.format_payload(
            "lead.created",
            {
                "timestamp": "2026-05-27T14:30:00Z",
                "organization_id": 7,
                "data": {"lead": {"id": 5}},
                "triggered_by": {"id": 10},
            },
            "custom",
        )
        assert payload["event"] == "lead.created"
        assert payload["organization_id"] == 7
        assert "data" in payload


def test_signature_generation_and_verification(app):
    with app.app_context():
        body = b'{"event":"lead.created"}'
        signature = WebhookService.sign_payload("secret", body)
        assert signature.startswith("sha256=")
        assert WebhookService.verify_signature("secret", body, signature) is True
        assert WebhookService.verify_signature("wrong", body, signature) is False


def test_failed_delivery_logged(app):
    org_id, _, _ = _setup_org(app, "failed-log")
    with app.app_context():
        _create_endpoint(org_id)
        with patch("app.webhooks.services.requests.post", side_effect=RuntimeError("boom")):
            WebhookService.dispatch("lead.created", {"lead": {"id": 1}}, org_id)
            db.session.commit()
        delivery = WebhookDelivery.query.first()
        assert delivery is not None
        assert delivery.status == "failed"
        assert "boom" in (delivery.response_body or "")


def test_non_2xx_delivery_marked_failed(app):
    org_id, _, _ = _setup_org(app, "non2xx")
    with app.app_context():
        _create_endpoint(org_id)
        with patch("app.webhooks.services.requests.post", return_value=_response(500, "error")):
            WebhookService.dispatch("lead.created", {"lead": {"id": 1}}, org_id)
            db.session.commit()
        delivery = WebhookDelivery.query.first()
        assert delivery.status == "failed"
        assert delivery.response_status == 500


def test_success_delivery_marked_delivered(app):
    org_id, _, _ = _setup_org(app, "success")
    with app.app_context():
        _create_endpoint(org_id)
        with patch("app.webhooks.services.requests.post", return_value=_response(204, "")):
            WebhookService.dispatch("lead.created", {"lead": {"id": 1}}, org_id)
            db.session.commit()
        delivery = WebhookDelivery.query.first()
        assert delivery.status == "delivered"


def test_test_endpoint_sends_sample_payload(app):
    org_id, user_id, _ = _setup_org(app, "test-send")
    with app.app_context():
        endpoint = _create_endpoint(org_id)
        with patch("app.webhooks.services.requests.post", return_value=_response(200, "ok")):
            result = WebhookService.send_test(endpoint.id, user_id)
            db.session.commit()
        assert result["success"] is True
        assert WebhookDelivery.query.filter_by(endpoint_id=endpoint.id).count() == 1


def test_inactive_endpoint_does_not_receive_events(app):
    org_id, _, _ = _setup_org(app, "inactive")
    with app.app_context():
        _create_endpoint(org_id, is_active=False)
        with patch("app.webhooks.services.requests.post") as mocked:
            result = WebhookService.dispatch("lead.created", {"lead": {"id": 1}}, org_id)
            db.session.commit()
        assert result["matched_endpoints"] == 0
        mocked.assert_not_called()


def test_unsubscribed_event_endpoint_does_not_receive(app):
    org_id, _, _ = _setup_org(app, "event-filter")
    with app.app_context():
        _create_endpoint(org_id, events=["proposal.viewed"])
        with patch("app.webhooks.services.requests.post") as mocked:
            result = WebhookService.dispatch("lead.created", {"lead": {"id": 1}}, org_id)
            db.session.commit()
        assert result["matched_endpoints"] == 0
        mocked.assert_not_called()


def test_cross_tenant_isolation_for_dispatch(app):
    org_a, _, _ = _setup_org(app, "tenant-a")
    org_b, _, _ = _setup_org(app, "tenant-b")
    with app.app_context():
        _create_endpoint(org_a)
        _create_endpoint(org_b)
        with patch("app.webhooks.services.requests.post", return_value=_response(200, "ok")):
            result = WebhookService.dispatch("lead.created", {"lead": {"id": 1}}, org_a)
            db.session.commit()
        assert result["matched_endpoints"] == 1
        assert WebhookDelivery.query.filter_by(organization_id=org_b).count() == 0


def test_retry_runs_only_once(app):
    org_id, _, _ = _setup_org(app, "retry-once")
    with app.app_context():
        endpoint = _create_endpoint(org_id)
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            organization_id=org_id,
            event_type="lead.created",
            payload={"event": "lead.created", "data": {"lead": {"id": 1}}},
            status="failed",
            retry_count=0,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=6),
        )
        db.session.add(delivery)
        db.session.commit()
        with patch("app.webhooks.services.requests.post", return_value=_response(200, "ok")) as mocked:
            first = WebhookService.retry_pending_deliveries()
            second = WebhookService.retry_pending_deliveries()
            db.session.commit()
        db.session.refresh(delivery)
        assert first == 1
        assert second == 0
        assert delivery.retry_count == 1
        assert mocked.call_count == 1


def test_retry_ignores_delivered_deliveries(app):
    org_id, _, _ = _setup_org(app, "retry-ignore")
    with app.app_context():
        endpoint = _create_endpoint(org_id)
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            organization_id=org_id,
            event_type="lead.created",
            payload={"event": "lead.created"},
            status="delivered",
            retry_count=0,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=6),
        )
        db.session.add(delivery)
        db.session.commit()
        with patch("app.webhooks.services.requests.post") as mocked:
            count = WebhookService.retry_pending_deliveries()
        assert count == 0
        mocked.assert_not_called()


def test_delete_endpoint_does_not_leak_deliveries_across_orgs(app, client):
    org_a, _, email_a = _setup_org(app, "delete-a")
    org_b, _, _ = _setup_org(app, "delete-b")
    with app.app_context():
        _create_endpoint(org_a)
        endpoint_b = _create_endpoint(org_b)
        endpoint_b_id = endpoint_b.id
        delivery_b = WebhookDelivery(
            endpoint_id=endpoint_b.id,
            organization_id=org_b,
            event_type="lead.created",
            payload={"event": "lead.created"},
            status="failed",
        )
        db.session.add(delivery_b)
        db.session.commit()

    _login(client, email_a)
    response = client.post(f"/settings/webhooks/{endpoint_b_id}/delete")
    assert response.status_code == 404
    with app.app_context():
        assert WebhookDelivery.query.filter_by(organization_id=org_b).count() == 1


def test_settings_page_renders(app, client):
    _, _, email = _setup_org(app, "settings-render")
    _login(client, email)
    response = client.get("/settings/webhooks")
    assert response.status_code == 200
    assert b"Ulosp" in response.data and b"webhook" in response.data.lower()

