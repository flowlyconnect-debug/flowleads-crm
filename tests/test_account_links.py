"""Links in account e-mails (customer invite, password reset) point at
APP_BASE_URL and bypass Mailgun click tracking."""

import json
from unittest.mock import patch

from app.core.urls import external_url
from app.email.services import EmailService


def test_external_url_uses_app_base_url(app):
    app.config["APP_BASE_URL"] = "https://crm.example.fi/"
    with app.test_request_context("/", base_url="http://127.0.0.1:5000"):
        assert external_url("invite.accept_invite", token="abc") == "https://crm.example.fi/invite/abc"


def test_external_url_falls_back_to_request_host(app):
    app.config["APP_BASE_URL"] = ""
    with app.test_request_context("/"):
        url = external_url("auth.login")
    assert url.startswith("http") and url.endswith("/auth/login")


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps({"id": "<x@mg>"}).encode()


def _sent_form(app, send):
    app.config.update(MAILGUN_API_KEY="key", MAILGUN_DOMAIN="mg.example.fi", EMAIL_SENDING_ENABLED=True)
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["data"] = request.data.decode()
        return _Resp()

    with app.app_context(), patch("app.email.services.email_sending_enabled", return_value=True), patch(
        "urllib.request.urlopen", side_effect=fake_urlopen
    ):
        result = send()
    assert result["success"], result
    return captured["data"]


def test_customer_invite_mail_turns_click_tracking_off(app):
    data = _sent_form(
        app,
        lambda: EmailService.send_customer_invite_email(
            to_email="asiakas@example.fi",
            invite_url="https://crm.example.fi/invite/abc",
            organization_name="Testi Oy",
            ttl_days=7,
        ),
    )
    assert "o%3Atracking-clicks=no" in data
    assert "crm.example.fi%2Finvite%2Fabc" in data


def test_password_reset_mail_turns_click_tracking_off(app):
    data = _sent_form(
        app,
        lambda: EmailService.send_password_reset_email(
            to_email="user@example.fi", reset_url="https://crm.example.fi/auth/reset/abc"
        ),
    )
    assert "o%3Atracking-clicks=no" in data
