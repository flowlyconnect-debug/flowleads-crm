"""Database migration health checks."""

from __future__ import annotations

from flask import Flask
from flask_migrate import current, heads
from sqlalchemy import inspect

from app.extensions import db


def get_migration_status() -> dict:
    """Compare applied Alembic revision to repository head."""
    try:
        current_rev = current()
        head_rev = heads()
    except Exception as exc:
        return {
            "ok": False,
            "current": None,
            "head": None,
            "pending": True,
            "error": str(exc),
        }

    current_value = current_rev if isinstance(current_rev, str) else None
    if isinstance(head_rev, (list, tuple)):
        head_value = head_rev[0] if head_rev else None
    else:
        head_value = head_rev

    pending = bool(head_value and current_value != head_value)
    return {
        "ok": not pending and current_value is not None,
        "current": current_value,
        "head": head_value,
        "pending": pending,
        "error": None,
    }


def get_schema_probe() -> dict:
    """Check columns/tables introduced in recent migrations."""
    try:
        inspector = inspect(db.engine)
        if "leads" not in inspector.get_table_names():
            return {
                "ok": False,
                "missing_lead_columns": ["industry", "region"],
                "missing_tables": ["org_lead_settings"],
                "error": "leads table missing",
            }
        lead_columns = {column["name"] for column in inspector.get_columns("leads")}
        tables = set(inspector.get_table_names())
        missing_lead_columns = [name for name in ("industry", "region") if name not in lead_columns]
        missing_tables = [name for name in ("org_lead_settings",) if name not in tables]
        ok = not missing_lead_columns and not missing_tables
        return {
            "ok": ok,
            "missing_lead_columns": missing_lead_columns,
            "missing_tables": missing_tables,
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "missing_lead_columns": [],
            "missing_tables": [],
            "error": str(exc),
        }


def log_migration_status(app: Flask) -> None:
    with app.app_context():
        migration = get_migration_status()
        schema = get_schema_probe()
        if migration["pending"] or not schema["ok"]:
            app.logger.error(
                "Database schema out of date: current=%s head=%s missing_columns=%s missing_tables=%s — run `flask db upgrade`",
                migration.get("current"),
                migration.get("head"),
                schema.get("missing_lead_columns"),
                schema.get("missing_tables"),
            )
        else:
            app.logger.info(
                "Database schema OK (revision=%s)",
                migration.get("current"),
            )


def full_health_report() -> dict:
    migration = get_migration_status()
    schema = get_schema_probe()
    schema_ok = bool(schema.get("ok"))
    current = migration.get("current")
    pending = bool(migration.get("pending"))

    if not schema_ok:
        ok = False
    elif current is None:
        # Tests/dev may use db.create_all() without an alembic stamp.
        ok = True
    else:
        ok = not pending

    return {
        "ok": ok,
        "migration": migration,
        "schema": schema,
    }
