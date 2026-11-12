"""Database backup without pg_dump (app/backups/logical.py)."""

from __future__ import annotations

import gzip
import json
import tarfile
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pyotp
import pytest

from app.backups import services as backups
from app.backups.logical import LogicalBackupError, dump_database, read_header, restore_database
from app.extensions import db
from app.leads.models import Lead
from app.users.models import User
from app.users.services import create_organization, create_user


@pytest.fixture
def backup_dir(app, tmp_path):
    path = tmp_path / "backups"
    path.mkdir()
    app.config["BACKUP_DIR"] = str(path)
    return path


def _data():
    org = create_organization("Varmuus Oy", "varmuus")
    db.session.flush()
    from app.api.services import upsert_lead

    lead, _ = upsert_lead(
        org.id,
        {"company": "As Oy Tallessa", "source": "import", "source_ref": "import-1", "metadata": {"$dt": "not a tag", "n": [1, 2]}},
        match_by_email=False,
    )
    lead.deal_value = Decimal("12345.67")
    lead.stage_changed_at = datetime(2026, 11, 1, 12, 30, tzinfo=timezone.utc)
    db.session.commit()
    return org, lead


def test_dump_and_restore_round_trip(app, tmp_path):
    with app.app_context():
        org, lead = _data()
        lead_id = lead.id
        dest = tmp_path / "db.jsonl.gz"
        info = dump_database(db.engine, db.metadata, dest)
        assert info["rows"] > 0 and info["tables"] > 10
        header = read_header(dest)
        assert header["format"] == "flowleads-logical" and "leads" in header["tables"]

        # change data, then restore
        db.session.get(Lead, lead_id).company = "Muutettu"
        create_organization("Myöhemmin Oy", "myohemmin")
        db.session.commit()
        result = restore_database(db.engine, db.metadata, dest)
        assert result["counts"]["leads"] == 1
        db.session.expire_all()
        restored = db.session.get(Lead, lead_id)
        assert restored.company == "As Oy Tallessa"
        assert restored.deal_value == Decimal("12345.67")
        # JSON content that looks like a tag stays JSON
        assert restored.ai_contact_info == {"$dt": "not a tag", "n": [1, 2]}
        from app.users.models import Organization

        assert {o.slug for o in Organization.query.all()} == {"varmuus"}


def test_restore_refuses_another_schema(app, tmp_path):
    with app.app_context():
        _data()
        dest = tmp_path / "db.jsonl.gz"
        dump_database(db.engine, db.metadata, dest)
        lines = gzip.open(dest, "rt", encoding="utf-8").read().splitlines()
        header = json.loads(lines[0])
        header["alembic_revision"] = "something-else"
        with gzip.open(dest, "wt", encoding="utf-8") as out:
            out.write("\n".join([json.dumps(header), *lines[1:]]) + "\n")
        with pytest.raises(LogicalBackupError):
            restore_database(db.engine, db.metadata, dest)
        bad = tmp_path / "bad.jsonl.gz"
        with gzip.open(bad, "wt") as out:
            out.write('{"format": "other"}\n')
        with pytest.raises(LogicalBackupError):
            read_header(bad)


def test_format_choice(app):
    with app.app_context():
        app.config["SQLALCHEMY_DATABASE_URI_BACKUP"] = app.config["SQLALCHEMY_DATABASE_URI"]
        app.config["BACKUP_DB_FORMAT"] = "python"
        assert backups._database_dump_format() == "python"
        app.config["BACKUP_DB_FORMAT"] = "auto"
        with patch.object(backups, "_is_postgres_uri", return_value=True):
            with patch.object(backups, "_pg_dump_major", return_value=None):
                assert backups._database_dump_format() == "python"  # no pg_dump
            with patch.object(backups, "_pg_dump_major", return_value=16), patch.object(backups, "_server_major", return_value=18):
                assert backups._database_dump_format() == "python"  # too old for the server
            with patch.object(backups, "_pg_dump_major", return_value=18), patch.object(backups, "_server_major", return_value=18):
                assert backups._database_dump_format() == "pg_dump"


def test_create_and_restore_backup_with_python_dump(app, backup_dir):
    with app.app_context():
        app.config["BACKUP_DB_FORMAT"] = "python"
        try:
            org, lead = _data()
            lead_id = lead.id
            admin = create_user("sa-logical@test.com", "securepassword1", role="superadmin")
            secret = pyotp.random_base32()
            admin.totp_secret = secret
            admin.totp_enabled = True
            db.session.commit()
            admin_id = admin.id
            filename = backups.create_backup(triggered_by_user_id=admin_id)
            with tarfile.open(backup_dir / filename) as tar:
                names = tar.getnames()
                manifest = json.load(tar.extractfile("manifest.json"))
            assert "database.jsonl.gz" in names and "database.sql.gz" not in names
            assert manifest["database"]["format"] == "python" and manifest["database"]["rows"] > 0

            db.session.get(Lead, lead_id).company = "Muutettu"
            db.session.commit()
            with patch.object(backups, "verify_totp_token", return_value=True, create=True), patch(
                "app.backups.services._verify_superadmin_restore"
            ):
                backups.restore_backup(filename, confirmed_by_user_id=admin_id, totp_code="000000", password="securepassword1")
            db.session.expire_all()
            assert db.session.get(Lead, lead_id).company == "As Oy Tallessa"
            assert User.query.filter_by(email="sa-logical@test.com").count() == 1
        finally:
            app.config["BACKUP_DB_FORMAT"] = "auto"
