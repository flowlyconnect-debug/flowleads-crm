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


def _s3_enabled() -> bool:
    return (current_app.config.get("BACKUP_STORAGE_BACKEND") or "local").lower() == "s3"


def _s3_bucket() -> str:
    bucket = current_app.config.get("BACKUP_S3_BUCKET")
    if not bucket:
        raise BackupServiceError("BACKUP_S3_BUCKET is not configured.", "s3_not_configured")
    return bucket


def _s3_prefix() -> str:
    return (current_app.config.get("BACKUP_S3_PREFIX") or "").strip("/")


def _s3_key(filename: str) -> str:
    prefix = _s3_prefix()
    return f"{prefix}/{filename}" if prefix else filename


def _s3_client(*, readonly: bool = False):
    """Build an S3-compatible client. readonly=True uses the web admin UI's
    restricted read-only credentials (falls back to the read-write ones if no
    separate read-only key was configured)."""
    import boto3

    access_key = current_app.config.get("BACKUP_S3_ACCESS_KEY_ID")
    secret_key = current_app.config.get("BACKUP_S3_SECRET_ACCESS_KEY")
    if readonly:
        access_key = current_app.config.get("BACKUP_S3_READONLY_ACCESS_KEY_ID") or access_key
        secret_key = current_app.config.get("BACKUP_S3_READONLY_SECRET_ACCESS_KEY") or secret_key

    return boto3.client(
        "s3",
        endpoint_url=current_app.config.get("BACKUP_S3_ENDPOINT_URL") or None,
        region_name=current_app.config.get("BACKUP_S3_REGION") or None,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _upload_backup_to_s3(path: Path, filename: str) -> None:
    try:
        _s3_client().upload_file(str(path), _s3_bucket(), _s3_key(filename))
    except BackupServiceError:
        raise
    except Exception as exc:
        raise BackupServiceError(f"S3 upload failed: {exc}", "s3_upload_failed") from exc


def _cleanup_old_backups_s3() -> int:
    retention_days = int(current_app.config.get("BACKUP_RETENTION_DAYS", 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    client = _s3_client()
    bucket = _s3_bucket()
    prefix = _s3_prefix()
    to_delete = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/" if prefix else ""):
        for obj in page.get("Contents", []):
            name = obj["Key"].rsplit("/", 1)[-1]
            if not BACKUP_FILENAME_RE.match(name):
                continue
            last_modified = obj["LastModified"]
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)
            if last_modified < cutoff:
                to_delete.append({"Key": obj["Key"]})
    for i in range(0, len(to_delete), 1000):
        batch = to_delete[i : i + 1000]
        client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
    return len(to_delete)


def _list_backups_s3() -> list[dict]:
    client = _s3_client(readonly=True)
    bucket = _s3_bucket()
    prefix = _s3_prefix()
    items = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/" if prefix else ""):
        for obj in page.get("Contents", []):
            name = obj["Key"].rsplit("/", 1)[-1]
            if not BACKUP_FILENAME_RE.match(name):
                continue
            last_modified = obj["LastModified"]
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)
            items.append(
                {
                    "filename": name,
                    "size": obj.get("Size", 0),
                    "created_at": last_modified,
                    "status": "available",
                }
            )
    items.sort(key=lambda i: i["created_at"], reverse=True)
    return items


def backup_exists(filename: str) -> bool:
    """Cheap existence check that never downloads the file (used by the restore
    confirmation page)."""
    name = sanitize_backup_filename(filename)
    if _s3_enabled():
        try:
            _s3_client(readonly=True).head_object(Bucket=_s3_bucket(), Key=_s3_key(name))
            return True
        except Exception:
            return False
    return (_backup_dir() / name).is_file()


def download_backup_to_temp(filename: str) -> Path:
    """Return a local path to the backup, downloading it from S3 to a fresh temp
    file first if that's where backups live. Callers that got a temp download are
    responsible for nothing extra — it's cleaned up on next reboot/tmp sweep — but
    for a long-lived process prefer deleting it once done."""
    name = sanitize_backup_filename(filename)
    if not _s3_enabled():
        return get_backup_path(name)

    dest_dir = Path(tempfile.mkdtemp(prefix="flowleads_backup_dl_"))
    dest = dest_dir / name
    try:
        _s3_client(readonly=True).download_file(_s3_bucket(), _s3_key(name), str(dest))
    except Exception as exc:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise BackupServiceError("Backup file not found.", "not_found") from exc
    return dest


def _uploads_dir() -> Path:
    raw = current_app.config.get("UPLOAD_DIR", "./uploads")
    return Path(raw).resolve()


def _timestamp_filename(offset_seconds: int = 0) -> str:
    from datetime import timedelta

    ts = (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime("%Y_%m_%d_%H%M%S")
    return f"backup_{ts}.tar.gz"


def _free_filename() -> str:
    """A name no backup has yet: two backups within one second (the safety
    copy taken right before a restore) must not overwrite each other."""
    for offset in range(60):
        name = _timestamp_filename(offset)
        try:
            taken = backup_exists(name)
        except Exception:
            taken = False
        if not taken:
            return name
    return _timestamp_filename(60)


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


def _pg_dump_major() -> int | None:
    """Major version of the pg_dump on PATH, None when it is missing."""
    import re

    try:
        out = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)(?:\.\d+)?", out.stdout or "")
    return int(match.group(1)) if match else None


def _server_major() -> int | None:
    try:
        number = db.session.execute(db.text("SHOW server_version_num")).scalar()
        return int(number) // 10000
    except Exception:
        db.session.rollback()
        return None


def _database_dump_format() -> str:
    """"pg_dump" or "python" (app/backups/logical.py). BACKUP_DB_FORMAT:
    auto (default) = pg_dump when it exists and is at least the server's
    version (pg_dump cannot dump a newer server), python otherwise."""
    wanted = (current_app.config.get("BACKUP_DB_FORMAT") or "auto").lower()
    if wanted in ("python", "pg_dump"):
        return wanted
    if not _is_postgres_uri(current_app.config.get("SQLALCHEMY_DATABASE_URI")):
        return "pg_dump"  # tests: placeholder dump; SQLite is never production
    client, server = _pg_dump_major(), _server_major()
    if client is None or (server is not None and client < server):
        logger.info("Using the Python database dump (pg_dump %s, server %s)", client, server)
        return "python"
    return "pg_dump"


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
        from app.email.services import send_system_message

        send_system_message(msg)
    except Exception:
        logger.exception("Failed to send backup notification email")


def create_backup(*, triggered_by_user_id: int | None = None) -> str:
    """Create a full backup archive. Returns filename on success."""
    backup_dir = _backup_dir()
    filename = _free_filename()
    archive_path = backup_dir / filename

    work_dir = Path(tempfile.mkdtemp(prefix="flowleads_backup_"))
    try:
        db_dump = work_dir / "database.sql.gz"
        db_format = _database_dump_format()
        dump_info: dict = {"format": db_format}
        if db_format == "python":
            from app.backups.logical import dump_database

            dump_info.update(dump_database(db.engine, db.metadata, work_dir / "database.jsonl.gz"))
        elif current_app.config.get("TESTING") and not _is_postgres_uri(
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
            "database": dump_info,
        }
        (work_dir / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        with tarfile.open(archive_path, "w:gz") as tar:
            for item in work_dir.iterdir():
                tar.add(item, arcname=item.name)

        if _s3_enabled():
            _upload_backup_to_s3(archive_path, filename)
            removed = _cleanup_old_backups_s3()
            archive_path.unlink(missing_ok=True)
        else:
            removed = _cleanup_old_backups(backup_dir)

        log_audit(
            "backup_created",
            user_id=triggered_by_user_id,
            target_type="backup",
            metadata={"filename": filename, "retention_removed": removed, "database_format": db_format},
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
    if _s3_enabled():
        return _list_backups_s3()
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
    from app.auth.totp_utils import fresh_user_totp_secret, verify_totp_token

    secret = fresh_user_totp_secret(user)
    if not verify_totp_token(
        secret, totp_code, context="backup_restore", user_id=user.id
    ):
        raise BackupServiceError("Invalid 2FA code.", "invalid_totp")


def _restore_pg_dump(sql_gz: Path, database_url: str | None = None) -> None:
    database_url = database_url or current_app.config.get("SQLALCHEMY_DATABASE_URI")
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
    target_database_url: str | None = None,
) -> None:
    """Restore from backup after superadmin password + TOTP verification.

    By default this restores IN PLACE, into this app's own database — that is
    what a real disaster-recovery restore needs to do. For a restore DRILL
    (testing that backups are actually restorable, without touching
    production), pass `target_database_url` pointing at a separate PostgreSQL
    database instead; the dump is applied there and this app's own database
    is never touched.
    """
    user = db.session.get(User, confirmed_by_user_id)
    if not user:
        raise BackupServiceError("User not found.", "not_found")

    _verify_superadmin_restore(user, password, totp_code)
    archive_path = download_backup_to_temp(backup_filename)
    downloaded_dir = archive_path.parent if _s3_enabled() else None

    safety_name = None
    if target_database_url is None:
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
        logical_dump = extract_dir / "database.jsonl.gz"
        if logical_dump.is_file():
            from app.backups.logical import LogicalBackupError, restore_database

            import sqlalchemy as sa

            engine = db.engine
            if target_database_url is not None:
                engine = sa.create_engine(target_database_url)
            try:
                restore_database(engine, db.metadata, logical_dump)
            except LogicalBackupError as exc:
                raise BackupServiceError(f"Database restore failed: {exc.message}", "restore_failed") from exc
            finally:
                if target_database_url is not None:
                    engine.dispose()
            db.session.expire_all()
        elif db_dump.is_file():
            if current_app.config.get("TESTING") and not _is_postgres_uri(
                target_database_url or current_app.config.get("SQLALCHEMY_DATABASE_URI")
            ):
                pass
            else:
                _restore_pg_dump(db_dump, target_database_url)

        uploads_backup = extract_dir / "uploads"
        if target_database_url is None:
            # Uploads live on local disk, not in the database — only restore them
            # for a real in-place restore, never for a drill into another DB.
            uploads_dest = _uploads_dir()
            if uploads_backup.is_dir():
                uploads_dest.mkdir(parents=True, exist_ok=True)
                if uploads_dest.exists():
                    shutil.rmtree(uploads_dest)
                shutil.copytree(uploads_backup, uploads_dest)

        log_audit(
            "backup_restored",
            # the restored data may not contain the user who restored it
            user_id=confirmed_by_user_id if db.session.get(User, confirmed_by_user_id) else None,
            target_type="backup",
            metadata={
                "filename": sanitize_backup_filename(backup_filename),
                "safety_backup": safety_name,
                "drill_into_separate_database": target_database_url is not None,
            },
        )
        db.session.commit()

        if target_database_url is None:
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
        if downloaded_dir is not None:
            shutil.rmtree(downloaded_dir, ignore_errors=True)
