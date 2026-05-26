"""Backup and restore service for FlowLeads CRM."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import current_app
from flask_mail import Message

from app.core.audit import log_audit
from app.core.security import check_password
from app.email.models import EmailTemplate
from app.extensions import db, mail
from app.users.models import Organization, User

logger = logging.getLogger(__name__)

BACKUP_FILENAME_RE = re.compile(r"^backup_\d{4}_\d{2}_\d{2}_\d{6}\.tar\.gz$")

SENSITIVE_EXPORT_KEYS = frozenset(
    {
        "secret_key",
        "database_url",
        "mailgun_api_key",
        "openai_api_key",
        "password",
        "password_hash",
        "totp_secret",
        "key_hash",
        "api_key",
    }
)


class BackupServiceError(Exception):
    def __init__(self, message: str, code: str = "backup_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _backup_dir() -> Path:
    raw = current_app.config.get("BACKUP_DIR", "./backups")
    path = Path(raw).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _uploads_dir() -> Path:
    raw = current_app.config.get("UPLOAD_DIR", "./uploads")
    return Path(raw).resolve()


def _timestamp_filename() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y_%m_%d_%H%M%S")
    return f"backup_{ts}.tar.gz"


def sanitize_backup_filename(filename: str) -> str:
    """Validate backup filename; reject path traversal."""
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise BackupServiceError("Invalid backup filename.", "invalid_filename")
    name = os.path.basename(filename.strip())
    if not BACKUP_FILENAME_RE.match(name):
        raise BackupServiceError("Invalid backup filename.", "invalid_filename")
    return name


def _is_postgres_uri(database_url: str | None) -> bool:
    if not database_url:
        return False
    return database_url.startswith("postgresql") or database_url.startswith("postgres://")


def _parse_database_url(database_url: str) -> dict:
    parsed = urlparse(database_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "",
        "password": parsed.password or "",
        "dbname": (parsed.path or "/").lstrip("/"),
    }


def _run_pg_dump(dest_sql_gz: Path) -> None:
    database_url = current_app.config.get("SQLALCHEMY_DATABASE_URI")
    if not _is_postgres_uri(database_url):
        raise BackupServiceError(
            "Database backup requires PostgreSQL (pg_dump).",
            "unsupported_database",
        )

    params = _parse_database_url(database_url)
    env = os.environ.copy()
    if params["password"]:
        env["PGPASSWORD"] = params["password"]

    cmd = [
        "pg_dump",
        "-h",
        params["host"],
        "-p",
        params["port"],
        "-U",
        params["user"],
        "-d",
        params["dbname"],
        "--no-password",
        "-F",
        "p",
    ]
    try:
        with subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        ) as proc:
            assert proc.stdout is not None
            import gzip

            with gzip.open(dest_sql_gz, "wb") as gz:
                shutil.copyfileobj(proc.stdout, gz)
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            proc.wait()
            if proc.returncode != 0:
                safe_err = _sanitize_shell_error(stderr)
                raise BackupServiceError(
                    f"Database dump failed: {safe_err}",
                    "pg_dump_failed",
                )
    except FileNotFoundError:
        raise BackupServiceError("pg_dump not found. Install postgresql-client.", "pg_dump_missing") from None
    except BackupServiceError:
        raise
    except OSError as exc:
        raise BackupServiceError("Database dump failed.", "pg_dump_failed") from exc


def _sanitize_shell_error(text: str) -> str:
    """Remove lines that might contain credentials."""
    if not text:
        return "unknown error"
    lines = []
    for line in text.splitlines():
        lower = line.lower()
        if any(s in lower for s in ("password", "secret", "pgpassword")):
            continue
        lines.append(line[:200])
    return "; ".join(lines[:5]) or "unknown error"


def _export_email_templates() -> list[dict]:
    templates = EmailTemplate.query.order_by(EmailTemplate.id).all()
    return [
        {
            "id": t.id,
            "organization_id": t.organization_id,
            "name": t.name,
            "subject_template": t.subject_template,
            "body_html_template": t.body_html_template,
            "body_text_template": t.body_text_template,
            "variables": t.variables,
            "created_by": t.created_by,
        }
        for t in templates
    ]


def _export_system_settings() -> dict:
    orgs = Organization.query.order_by(Organization.id).all()
    users = User.query.order_by(User.id).all()
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "organizations": [
            {
                "id": o.id,
                "name": o.name,
                "slug": o.slug,
                "is_active": o.is_active,
                "email_from_name": o.email_from_name,
                "email_from_email": o.email_from_email,
                "mailgun_domain": o.mailgun_domain,
            }
            for o in orgs
        ],
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "role": u.role,
                "organization_id": u.organization_id,
                "is_active": u.is_active,
                "totp_enabled": u.totp_enabled,
            }
            for u in users
        ],
        "config_flags": {
            "email_sending_enabled": current_app.config.get("EMAIL_SENDING_ENABLED"),
            "ai_enrichment_enabled": current_app.config.get("AI_ENRICHMENT_ENABLED"),
            "public_registration_enabled": current_app.config.get("PUBLIC_REGISTRATION_ENABLED"),
        },
    }


def _redact_json_export(data: object) -> object:
    if isinstance(data, dict):
        return {
            k: _redact_json_export(v)
            for k, v in data.items()
            if str(k).lower() not in SENSITIVE_EXPORT_KEYS
            and not any(s in str(k).lower() for s in ("password", "secret", "hash", "api_key"))
        }
    if isinstance(data, list):
        return [_redact_json_export(item) for item in data]
    return data


def _cleanup_old_backups(backup_dir: Path) -> int:
    retention_days = int(current_app.config.get("BACKUP_RETENTION_DAYS", 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for path in backup_dir.iterdir():
        if not path.is_file():
            continue
        if not BACKUP_FILENAME_RE.match(path.name):
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _notify_superadmins(subject: str, body: str) -> None:
    if not current_app.config.get("EMAIL_SENDING_ENABLED", True):
        return
    try:
        superadmins = User.query.filter_by(role="superadmin", is_active=True).all()
        recipients = [u.email for u in superadmins if u.email]
        if not recipients:
            return
        msg = Message(
            subject=subject,
            recipients=recipients,
            body=body,
        )
        mail.send(msg)
    except Exception:
        logger.exception("Failed to send backup notification email")


def create_backup(*, triggered_by_user_id: int | None = None) -> str:
    """Create a full backup archive. Returns filename on success."""
    backup_dir = _backup_dir()
    filename = _timestamp_filename()
    archive_path = backup_dir / filename

    work_dir = Path(tempfile.mkdtemp(prefix="flowleads_backup_"))
    try:
        db_dump = work_dir / "database.sql.gz"
        if current_app.config.get("TESTING") and not _is_postgres_uri(
            current_app.config.get("SQLALCHEMY_DATABASE_URI")
        ):
            import gzip

            with gzip.open(db_dump, "wb") as gz:
                gz.write(b"-- test backup placeholder\n")
        else:
            _run_pg_dump(db_dump)

        templates_path = work_dir / "email_templates.json"
        settings_path = work_dir / "system_settings.json"
        templates_path.write_text(
            json.dumps(_redact_json_export(_export_email_templates()), indent=2),
            encoding="utf-8",
        )
        settings_path.write_text(
            json.dumps(_redact_json_export(_export_system_settings()), indent=2),
            encoding="utf-8",
        )

        uploads_src = _uploads_dir()
        if uploads_src.is_dir() and any(uploads_src.iterdir()):
            shutil.copytree(uploads_src, work_dir / "uploads", dirs_exist_ok=True)

        meta = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "triggered_by_user_id": triggered_by_user_id,
            "version": 1,
        }
        (work_dir / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        with tarfile.open(archive_path, "w:gz") as tar:
            for item in work_dir.iterdir():
                tar.add(item, arcname=item.name)

        removed = _cleanup_old_backups(backup_dir)

        log_audit(
            "backup_created",
            user_id=triggered_by_user_id,
            target_type="backup",
            metadata={"filename": filename, "retention_removed": removed},
        )
        db.session.commit()

        _notify_superadmins(
            "FlowLeads backup succeeded",
            f"Backup completed successfully.\nFile: {filename}\nOld backups removed: {removed}",
        )
        return filename

    except Exception as exc:
        db.session.rollback()
        safe_msg = str(exc) if isinstance(exc, BackupServiceError) else "Backup failed"
        log_audit(
            "backup_failed",
            user_id=triggered_by_user_id,
            target_type="backup",
            metadata={"error": safe_msg[:200]},
        )
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

        _notify_superadmins(
            "FlowLeads backup failed",
            f"Backup failed. Check server logs. Error: {safe_msg[:200]}",
        )
        if archive_path.exists():
            archive_path.unlink(missing_ok=True)
        if isinstance(exc, BackupServiceError):
            raise
        logger.exception("Backup failed")
        raise BackupServiceError("Backup failed.", "backup_failed") from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def list_backups() -> list[dict]:
    backup_dir = _backup_dir()
    items = []
    for path in sorted(backup_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file() or not BACKUP_FILENAME_RE.match(path.name):
            continue
        stat = path.stat()
        items.append(
            {
                "filename": path.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                "status": "available",
            }
        )
    return items


def get_backup_path(filename: str) -> Path:
    name = sanitize_backup_filename(filename)
    path = _backup_dir() / name
    if not path.is_file():
        raise BackupServiceError("Backup file not found.", "not_found")
    return path


def _verify_superadmin_restore(user: User, password: str, totp_code: str) -> None:
    if not user.is_superadmin():
        raise BackupServiceError("Only superadmin can restore backups.", "forbidden")
    if not user.totp_enabled or not user.totp_secret:
        raise BackupServiceError("2FA must be enabled for restore.", "2fa_required")
    if not check_password(password, user.password_hash):
        raise BackupServiceError("Invalid password.", "invalid_password")
    import pyotp

    totp = pyotp.TOTP(user.totp_secret)
    if not totp_code or not totp.verify(totp_code.strip(), valid_window=1):
        raise BackupServiceError("Invalid 2FA code.", "invalid_totp")


def _restore_pg_dump(sql_gz: Path) -> None:
    database_url = current_app.config.get("SQLALCHEMY_DATABASE_URI")
    if not _is_postgres_uri(database_url):
        raise BackupServiceError("Restore requires PostgreSQL.", "unsupported_database")

    params = _parse_database_url(database_url)
    env = os.environ.copy()
    if params["password"]:
        env["PGPASSWORD"] = params["password"]

    import gzip

    with gzip.open(sql_gz, "rb") as gz:
        sql_data = gz.read()

    cmd = [
        "psql",
        "-h",
        params["host"],
        "-p",
        params["port"],
        "-U",
        params["user"],
        "-d",
        params["dbname"],
        "--no-password",
        "-v",
        "ON_ERROR_STOP=1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=sql_data,
            capture_output=True,
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            safe_err = _sanitize_shell_error(proc.stderr.decode("utf-8", errors="replace"))
            raise BackupServiceError(f"Database restore failed: {safe_err}", "restore_failed")
    except FileNotFoundError:
        raise BackupServiceError("psql not found. Install postgresql-client.", "psql_missing") from None


def restore_backup(
    backup_filename: str,
    *,
    confirmed_by_user_id: int,
    totp_code: str,
    password: str,
) -> None:
    """Restore from backup after superadmin password + TOTP verification."""
    user = db.session.get(User, confirmed_by_user_id)
    if not user:
        raise BackupServiceError("User not found.", "not_found")

    _verify_superadmin_restore(user, password, totp_code)
    archive_path = get_backup_path(backup_filename)

    safety_name = None
    try:
        safety_name = create_backup(triggered_by_user_id=confirmed_by_user_id)
        logger.info("Safety backup created before restore: %s", safety_name)
    except BackupServiceError:
        logger.warning("Safety backup before restore failed; continuing restore")

    extract_dir = Path(tempfile.mkdtemp(prefix="flowleads_restore_"))
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(extract_dir, filter="data")

        db_dump = extract_dir / "database.sql.gz"
        if db_dump.is_file():
            if current_app.config.get("TESTING") and not _is_postgres_uri(
                current_app.config.get("SQLALCHEMY_DATABASE_URI")
            ):
                pass
            else:
                _restore_pg_dump(db_dump)

        uploads_backup = extract_dir / "uploads"
        uploads_dest = _uploads_dir()
        if uploads_backup.is_dir():
            uploads_dest.mkdir(parents=True, exist_ok=True)
            if uploads_dest.exists():
                shutil.rmtree(uploads_dest)
            shutil.copytree(uploads_backup, uploads_dest)

        log_audit(
            "backup_restored",
            user_id=confirmed_by_user_id,
            target_type="backup",
            metadata={"filename": sanitize_backup_filename(backup_filename), "safety_backup": safety_name},
        )
        db.session.commit()

        _notify_superadmins(
            "FlowLeads backup restored",
            f"Backup {backup_filename} was restored by {user.email}.",
        )

    except Exception as exc:
        db.session.rollback()
        safe_msg = str(exc) if isinstance(exc, BackupServiceError) else "Restore failed"
        log_audit(
            "backup_restore_failed",
            user_id=confirmed_by_user_id,
            target_type="backup",
            metadata={"filename": backup_filename, "error": safe_msg[:200]},
        )
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        if isinstance(exc, BackupServiceError):
            raise
        logger.exception("Restore failed")
        raise BackupServiceError("Restore failed.", "restore_failed") from exc
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
