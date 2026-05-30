"""Tests for temporary 500 diagnostics."""

from __future__ import annotations

import pytest

from app.core.diagnostics import capture_server_error, clear_last_error, get_last_error


@pytest.fixture(autouse=True)
def reset_last_error():
    clear_last_error()
    yield
    clear_last_error()


@pytest.fixture
def prod_app(app):
    app.config["FLASK_ENV"] = "production"
    app.config["DEBUG_DIAGNOSTICS"] = False
    return app


@pytest.fixture
def prod_diagnostics_app(app):
    app.config["FLASK_ENV"] = "production"
    app.config["DEBUG_DIAGNOSTICS"] = True
    return app


def test_capture_server_error_stores_context(app):
    with app.test_request_context("/leads?organization_id=1&search=acme"):
        capture_server_error(RuntimeError("boom"), hint="test hint")

    record = get_last_error()
    assert record is not None
    assert record["path"] == "/leads"
    assert record["args"] == {"organization_id": ["1"], "search": ["acme"]}
    assert record["organization_id"] == "1"
    assert record["error_type"] == "RuntimeError"
    assert record["error_message"] == "boom"
    assert "RuntimeError: boom" in record["traceback"]
    assert record["hint"] == "test hint"


def test_debug_last_error_disabled_in_production(client, prod_app):
    response = client.get("/api/v1/debug/last-error")
    assert response.status_code == 404


def test_debug_last_error_enabled_with_flag(client, prod_diagnostics_app):
    response = client.get("/api/v1/debug/last-error")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["error_type"] is None


def test_debug_last_error_returns_stored_record(client, app):
    app.config["FLASK_ENV"] = "development"

    with app.test_request_context("/leads?organization_id=7"):
        capture_server_error(ValueError("diagnostic test failure"))

    debug_response = client.get("/api/v1/debug/last-error")
    assert debug_response.status_code == 200
    payload = debug_response.get_json()["data"]
    assert payload["path"] == "/leads"
    assert payload["args"] == {"organization_id": ["7"]}
    assert payload["error_type"] == "ValueError"
    assert payload["error_message"] == "diagnostic test failure"
    assert "diagnostic test failure" in payload["traceback"]


def test_database_error_handler_captures(client, app):
    from sqlalchemy.exc import OperationalError

    app.config["FLASK_ENV"] = "development"
    with app.test_request_context("/tasks?organization_id=3"):
        capture_server_error(
            OperationalError("SELECT 1", {}, Exception("no such column: leads.industry")),
            hint="Database schema/query failure — ensure `flask db upgrade` has been applied",
        )

    debug_response = client.get("/api/v1/debug/last-error")
    payload = debug_response.get_json()["data"]
    assert payload["path"] == "/tasks"
    assert payload["error_type"] == "OperationalError"
    assert "no such column" in payload["error_message"]
