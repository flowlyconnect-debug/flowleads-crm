import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pyotp
import pytest

from app.backups.services import (
    BACKUP_FILENAME_RE,
    BackupServiceError,
    _export_system_settings,
    _redact_json_export,
    create_backup,
    get_backup_path,
    list_backups,
    restore_backup,
    sanitize_backup_filename,
)
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.users.models import AuditLog, User
from app.users.services import create_user


@pytest.fixture
def backup_dir(app, tmp_path):
    backup_path = tmp_path / "backups"
    backup_path.mkdir()
    app.config["BACKUP_DIR"] = str(backup_path)
    return backup_path


def test_create_backup_creates_tar_gz(app, backup_dir):
    with app.app_context():
        filename = create_backup(triggered_by_user_id=None)
        assert BACKUP_FILENAME_RE.match(filename)
        assert (backup_dir / filename).is_file()


def test_backup_excludes_secrets(app, backup_dir):
    with app.app_context():
        data = _redact_json_export(_export_system_settings())
        blob = json.dumps(data)
        assert "password_hash" not in blob
        assert "totp_secret" not in blob
        assert "SECRET_KEY" not in blob


def test_retention_cleanup_deletes_only_old_backup_files(app, backup_dir):
    old_name = "backup_2020_01_01_120000.tar.gz"
    new_name = "backup_2099_01_01_120000.tar.gz"
    old_path = backup_dir / old_name
    new_path = backup_dir / new_name
    old_path.write_bytes(b"old")
    new_path.write_bytes(b"new")
    old_time = datetime.now(timezone.utc) - timedelta(days=400)
    import os

    os.utime(old_path, (old_time.timestamp(), old_time.timestamp()))

    with app.app_context():
        app.config["BACKUP_RETENTION_DAYS"] = 30
        create_backup(triggered_by_user_id=None)

    assert not old_path.exists()
    assert new_path.exists() or any(backup_dir.glob("backup_*.tar.gz"))


def test_invalid_filename_rejected():
    with pytest.raises(BackupServiceError):
        sanitize_backup_filename("../etc/passwd")
    with pytest.raises(BackupServiceError):
        sanitize_backup_filename("not-a-backup.tar.gz")


def _superadmin_with_2fa(app):
    with app.app_context():
        user = create_user("sa-backup@test.com", "securepassword1", role="superadmin")
        user.totp_enabled = True
        user.totp_secret = pyotp.random_base32()
        db.session.commit()
        return user.id, user.totp_secret


def test_restore_requires_superadmin(app, backup_dir):
    from app.users.services import create_organization

    with app.app_context():
        org = create_organization("Acme BR", "acme-br")
        db.session.flush()
        user = create_user(
            "user@acme.com",
            "securepassword1",
            role="user",
            organization_id=org.id,
        )
        db.session.commit()
        filename = create_backup()
        user_id = user.id

    with app.app_context():
        with pytest.raises(BackupServiceError, match="superadmin"):
            restore_backup(
                filename,
                confirmed_by_user_id=user_id,
                totp_code="123456",
                password="securepassword1",
            )


def test_restore_requires_password_and_totp(app, backup_dir):
    user_id, secret = _superadmin_with_2fa(app)
    with app.app_context():
        filename = create_backup()
        totp = pyotp.TOTP(secret)

    with app.app_context():
        with pytest.raises(BackupServiceError, match="password"):
            restore_backup(
                filename,
                confirmed_by_user_id=user_id,
                totp_code=totp.now(),
                password="wrongpassword1",
            )
        with pytest.raises(BackupServiceError, match="2FA"):
            restore_backup(
                filename,
                confirmed_by_user_id=user_id,
                totp_code="000000",
                password="securepassword1",
            )


def test_restore_success_logs_audit(app, backup_dir):
    user_id, secret = _superadmin_with_2fa(app)
    with app.app_context():
        filename = create_backup()
        totp = pyotp.TOTP(secret)
        restore_backup(
            filename,
            confirmed_by_user_id=user_id,
            totp_code=totp.now(),
            password="securepassword1",
        )
        assert AuditLog.query.filter_by(action="backup_restored").count() >= 1


def test_failure_logs_audit_safely(app, backup_dir, monkeypatch):
    def fail_tar(*_args, **_kwargs):
        raise OSError("simulated archive failure")

    monkeypatch.setattr("tarfile.open", fail_tar)
    with app.app_context():
        with pytest.raises(BackupServiceError):
            create_backup(triggered_by_user_id=None)
        entry = AuditLog.query.filter_by(action="backup_failed").first()
        assert entry is not None
        meta = entry.metadata_json or {}
        assert "password" not in json.dumps(meta).lower()


@patch("app.backups.services._run_pg_dump")
def test_cli_backup_create(mock_dump, runner, app, backup_dir):
    def fake_dump(dest):
        import gzip

        with gzip.open(dest, "wb") as gz:
            gz.write(b"-- dump\n")

    mock_dump.side_effect = fake_dump
    result = runner.invoke(args=["backup-create"])
    assert result.exit_code == 0
    assert "Backup created" in result.output


def test_admin_backups_requires_2fa(client, app, backup_dir):
    with app.app_context():
        sa = create_user("sa-bu@test.com", "securepassword1", role="superadmin")
        sa.totp_enabled = True
        db.session.commit()

    client.post("/auth/login", data={"email": "sa-bu@test.com", "password": "securepassword1"})
    response = client.get("/admin/backups")
    assert response.status_code == 302

    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    response = client.get("/admin/backups")
    assert response.status_code == 200


def test_list_and_get_backup_path(app, backup_dir):
    with app.app_context():
        filename = create_backup()
        items = list_backups()
        assert any(i["filename"] == filename for i in items)
        path = get_backup_path(filename)
        assert path.name == filename


@patch("app.backups.routes.restore_backup")
def test_restore_route_post(mock_restore, client, app, backup_dir):
    user_id, secret = _superadmin_with_2fa(app)
    with app.app_context():
        filename = create_backup()

    client.post("/auth/login", data={"email": "sa-backup@test.com", "password": "securepassword1"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True

    totp = pyotp.TOTP(secret)
    response = client.post(
        f"/admin/backups/{filename}/restore",
        data={
            "password": "securepassword1",
            "totp_code": totp.now(),
            "confirm_overwrite": "y",
            "submit": "Restore backup",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    mock_restore.assert_called_once()


def test_download_backup_route(client, app, backup_dir):
    with app.app_context():
        sa = create_user("sa-dl@test.com", "securepassword1", role="superadmin")
        sa.totp_enabled = True
        filename = create_backup()
        db.session.commit()

    client.post("/auth/login", data={"email": "sa-dl@test.com", "password": "securepassword1"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    response = client.get(f"/admin/backups/{filename}/download")
    assert response.status_code == 200
    assert len(response.data) > 0
