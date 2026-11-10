"""Guardrails that must hold under production settings.

These are regression tests for the readiness-review findings: the dev-only
login bypass must not exist in the codebase at all, and dev tooling /
detailed error diagnostics must default to disabled regardless of how the
app is configured.
"""

from __future__ import annotations

import app as app_module
from app.config import Config, ProductionConfig


def test_dev_auto_login_removed_from_app_module():
    """The DEV_AUTO_LOGIN login/2FA bypass must not exist anywhere in app/__init__.py."""
    assert not hasattr(app_module, "_register_dev_auto_login")
    assert not hasattr(app_module, "DEV_AUTO_LOGIN_HOSTS")
    assert not hasattr(app_module, "DEV_AUTO_LOGIN_EMAIL")

    import inspect

    source = inspect.getsource(app_module)
    assert "DEV_AUTO_LOGIN" not in source


def test_require_2fa_has_no_dev_bypass_clause():
    import inspect

    from app.core import permissions as permissions_module

    assert "DEV_AUTO_LOGIN" not in inspect.getsource(permissions_module)


def test_production_config_disables_debug():
    assert ProductionConfig.DEBUG is False


def test_dev_tooling_flags_default_disabled(monkeypatch):
    monkeypatch.delenv("ENABLE_TEST_JOBS", raising=False)
    monkeypatch.delenv("DEBUG_DIAGNOSTICS", raising=False)
    monkeypatch.delenv("PUBLIC_REGISTRATION_ENABLED", raising=False)

    import importlib

    import app.config as config_module

    importlib.reload(config_module)
    try:
        assert config_module.Config.ENABLE_TEST_JOBS is False
        assert config_module.Config.DEBUG_DIAGNOSTICS is False
        assert config_module.Config.PUBLIC_REGISTRATION_ENABLED is False
    finally:
        importlib.reload(config_module)


def test_enable_test_jobs_requires_explicit_true(monkeypatch):
    monkeypatch.setenv("ENABLE_TEST_JOBS", "false")
    import importlib

    import app.config as config_module

    importlib.reload(config_module)
    try:
        assert config_module.Config.ENABLE_TEST_JOBS is False
    finally:
        monkeypatch.delenv("ENABLE_TEST_JOBS", raising=False)
        importlib.reload(config_module)
