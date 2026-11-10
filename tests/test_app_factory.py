"""Tests for ProxyFix and optional Sentry wiring in the app factory."""
from unittest.mock import patch

from flask import Flask, request

from app import _apply_proxy_fix, _init_sentry, create_app
from app.config import TestingConfig, apply_testing_defaults


def _bare_app(**config_overrides):
    """A minimal Flask app with just the config keys _apply_proxy_fix/_init_sentry read."""
    app = Flask(__name__)
    app.config["TESTING"] = False
    app.config["SENTRY_DSN"] = None
    app.config["SENTRY_TRACES_SAMPLE_RATE"] = 0.0
    app.config["PROXY_FIX_X_FOR"] = 1
    app.config.update(config_overrides)
    return app


def test_sentry_init_not_called_without_dsn():
    app = _bare_app(SENTRY_DSN=None)
    with patch("sentry_sdk.init") as mock_init:
        _init_sentry(app)
    mock_init.assert_not_called()


def test_sentry_init_called_with_dsn():
    app = _bare_app(SENTRY_DSN="https://example@sentry.example.com/1", SENTRY_TRACES_SAMPLE_RATE=0.25)
    with patch("sentry_sdk.init") as mock_init:
        _init_sentry(app)
    mock_init.assert_called_once()
    _, kwargs = mock_init.call_args
    assert kwargs["dsn"] == "https://example@sentry.example.com/1"
    assert kwargs["traces_sample_rate"] == 0.25


def test_proxy_fix_skipped_when_testing_flag_set():
    app = _bare_app(TESTING=True)
    _apply_proxy_fix(app)
    from werkzeug.middleware.proxy_fix import ProxyFix

    assert not isinstance(app.wsgi_app, ProxyFix)


def test_proxy_fix_applied_outside_testing_honors_forwarded_for():
    app = _bare_app(PROXY_FIX_X_FOR=1)
    from werkzeug.middleware.proxy_fix import ProxyFix

    _apply_proxy_fix(app)
    assert isinstance(app.wsgi_app, ProxyFix)

    seen_addr = {}

    @app.route("/_remote_addr_probe")
    def _probe():
        seen_addr["value"] = request.remote_addr
        return "ok"

    client = app.test_client()
    client.get(
        "/_remote_addr_probe",
        headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"},
        environ_overrides={"REMOTE_ADDR": "10.0.0.1"},
    )
    assert seen_addr["value"] == "203.0.113.9"


def test_real_app_factory_skips_proxy_fix_and_sentry_in_testing():
    with patch("sentry_sdk.init") as mock_init:
        app = create_app(TestingConfig)
        apply_testing_defaults(app)

    from werkzeug.middleware.proxy_fix import ProxyFix

    assert not isinstance(app.wsgi_app, ProxyFix)
    mock_init.assert_not_called()
