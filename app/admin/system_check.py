"""Järjestelmän tila: is production configured to send email, take backups and
keep secrets? Read-only checks of the configuration (values are never shown,
only whether they are set) and of the latest results in the audit and email
logs. Shown at /admin/system and by `flask system-check`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import current_app

from app.extensions import db

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


def _set(key: str) -> bool:
    return bool(str(current_app.config.get(key) or "").strip())


def _check(group: str, name: str, status: str, detail: str) -> dict:
    return {"group": group, "name": name, "status": status, "detail": detail}


def _latest(action: str):
    from app.users.models import AuditLog

    return AuditLog.query.filter_by(action=action).order_by(AuditLog.created_at.desc()).first()


def _when(entry) -> str:
    if entry is None:
        return "ei koskaan"
    from app import to_helsinki

    return to_helsinki(entry.created_at).strftime("%d.%m.%Y %H.%M")


def run_checks(*, now: datetime | None = None) -> list[dict]:
    config = current_app.config
    now = now or datetime.now(timezone.utc)
    checks: list[dict] = []

    # --- base -------------------------------------------------------------------
    base_url = str(config.get("APP_BASE_URL") or "")
    if not base_url:
        checks.append(_check("Perusasetukset", "APP_BASE_URL", FAIL, "Ei asetettu: sähköpostien ja webhookien linkit eivät toimi."))
    elif not base_url.startswith("https://") and not config.get("TESTING"):
        checks.append(_check("Perusasetukset", "APP_BASE_URL", WARN, f"{base_url} ei ole https-osoite."))
    else:
        checks.append(_check("Perusasetukset", "APP_BASE_URL", OK, base_url))
    for key, why in (
        ("CALENDAR_ENCRYPTION_KEY", "kalenteriyhteyksien salaus"),
        ("WEBHOOK_ENCRYPTION_KEY", "webhook-salaisuudet sekä HubSpot- ja Pipedrive-avaimet"),
        ("N8N_MASTER_SECRET", "n8n-rajapinta (kohdekatalogin tuonti)"),
    ):
        checks.append(_check("Perusasetukset", key, OK if _set(key) else FAIL, why + (": asetettu" if _set(key) else ": puuttuu")))
    checks.append(
        _check(
            "Perusasetukset",
            "REDIS_URL",
            OK if _set("REDIS_URL") else WARN,
            "Pyyntörajat jaetaan workerien kesken." if _set("REDIS_URL") else "Puuttuu: pyyntörajat ovat muistissa ja per worker.",
        )
    )
    checks.append(_check("Perusasetukset", "OPENAI_API_KEY", OK if _set("OPENAI_API_KEY") else INFO, "Asetettu" if _set("OPENAI_API_KEY") else "Ei asetettu: AI-rikastus ja tekoälyhaku pois."))
    checks.append(_check("Perusasetukset", "SENTRY_DSN", OK if _set("SENTRY_DSN") else INFO, "Virheseuranta päällä" if _set("SENTRY_DSN") else "Ei virheseurantaa."))

    # --- email ------------------------------------------------------------------
    domain = str(config.get("MAILGUN_DOMAIN") or "")
    from_email = str(config.get("MAILGUN_FROM_EMAIL") or "")
    if not _set("MAILGUN_API_KEY") or not domain:
        checks.append(_check("Sähköposti", "Mailgun", FAIL, "MAILGUN_API_KEY tai MAILGUN_DOMAIN puuttuu: sähköpostit eivät lähde."))
    elif domain.startswith("sandbox"):
        checks.append(
            _check(
                "Sähköposti",
                "Mailgun-domain",
                FAIL,
                f"{domain} on sandbox-domain: Mailgun toimittaa viestit vain erikseen valtuutetuille vastaanottajille. Vaihda oma vahvistettu domain.",
            )
        )
    else:
        checks.append(_check("Sähköposti", "Mailgun-domain", OK, domain))
    if from_email and domain and not from_email.lower().endswith("@" + domain.lower()) and not from_email.lower().endswith("." + domain.lower()):
        checks.append(_check("Sähköposti", "Lähettäjä", WARN, f"{from_email} ei ole domainissa {domain}: viestit voivat päätyä roskapostiin."))
    elif from_email:
        checks.append(_check("Sähköposti", "Lähettäjä", OK, from_email))
    checks.append(
        _check(
            "Sähköposti",
            "MAILGUN_WEBHOOK_SIGNING_KEY",
            OK if _set("MAILGUN_WEBHOOK_SIGNING_KEY") else WARN,
            "Avaukset ja klikkaukset kirjautuvat" if _set("MAILGUN_WEBHOOK_SIGNING_KEY") else "Puuttuu: avaus-, klikkaus- ja palautustiedot eivät päivity.",
        )
    )
    from app.email.models import EmailLog

    since = now - timedelta(days=7)
    sent = EmailLog.query.filter(EmailLog.created_at >= since).count()
    failed = EmailLog.query.filter(EmailLog.created_at >= since, EmailLog.status.in_(("failed", "bounced"))).count()
    status = OK if sent and not failed else (WARN if failed else INFO)
    checks.append(_check("Sähköposti", "Viimeiset 7 päivää", status, f"{sent} viestiä, {failed} epäonnistunut tai palautunut."))
    account_ok, account_fail = _latest("account_email_sent"), _latest("account_email_failed")
    if account_fail is not None and (account_ok is None or account_fail.created_at > account_ok.created_at):
        reason = (account_fail.metadata_json or {}).get("error") or (account_fail.metadata_json or {}).get("reason") or "syy tuntematon"
        checks.append(_check("Sähköposti", "Tiliviestit (kutsut, salasanat)", FAIL, f"Viimeisin epäonnistui {_when(account_fail)}: {reason}"))
    else:
        checks.append(_check("Sähköposti", "Tiliviestit (kutsut, salasanat)", OK if account_ok else INFO, f"Viimeisin onnistunut {_when(account_ok)}."))

    # --- backups ----------------------------------------------------------------
    backend = str(config.get("BACKUP_STORAGE_BACKEND") or "local")
    if backend == "s3":
        missing = [k for k in ("BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY_ID", "BACKUP_S3_SECRET_ACCESS_KEY") if not _set(k)]
        if missing:
            checks.append(
                _check(
                    "Varmuuskopiot",
                    "Tallennus (S3)",
                    FAIL if "BACKUP_S3_BUCKET" in missing else INFO,
                    "Puuttuu: " + ", ".join(missing) + ". (Web-palvelu tarvitsee vain lukuavaimen; kirjoitusavain kuuluu ajastimelle.)",
                )
            )
        else:
            checks.append(_check("Varmuuskopiot", "Tallennus (S3)", OK, f"Bucket {config.get('BACKUP_S3_BUCKET')}"))
    else:
        checks.append(_check("Varmuuskopiot", "Tallennus", WARN, "Paikallinen levy: Renderissä web ja ajastin eivät jaa levyä, ja levy tyhjenee uudelleenkäynnistyksessä. Käytä S3:a."))
    from app.backups import services as backups

    database_url = str(config.get("SQLALCHEMY_DATABASE_URI") or "")
    if database_url.startswith("postgres"):
        client, server = backups._pg_dump_major(), backups._server_major()
        method = backups._database_dump_format()
        detail = f"pg_dump {client or 'puuttuu'}, tietokanta {server or '?'}: käytetään "
        detail += "pg_dumpia." if method == "pg_dump" else "Python-dumppia (pg_dump puuttuu tai on palvelinta vanhempi)."
        checks.append(_check("Varmuuskopiot", "Tietokannan dumppi", OK, detail))
    created, failed_backup = _latest("backup_created"), _latest("backup_failed")
    if created is None:
        checks.append(
            _check(
                "Varmuuskopiot",
                "Viimeisin varmuuskopio",
                FAIL if failed_backup else WARN,
                "Ei yhtään onnistunutta." + (f" Viimeisin epäonnistui {_when(failed_backup)}: {(failed_backup.metadata_json or {}).get('error', '')}" if failed_backup else ""),
            )
        )
    else:
        age = now - (created.created_at if created.created_at.tzinfo else created.created_at.replace(tzinfo=timezone.utc))
        checks.append(
            _check(
                "Varmuuskopiot",
                "Viimeisin varmuuskopio",
                OK if age < timedelta(hours=36) else WARN,
                f"{_when(created)} ({(created.metadata_json or {}).get('filename', '')})"
                + (" – yli vuorokausi sitten, toimiiko ajastin?" if age >= timedelta(hours=36) else ""),
            )
        )

    # --- database -------------------------------------------------------------------
    try:
        revision = db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar()
        checks.append(_check("Tietokanta", "Migraatiot", OK, f"Versio {revision}"))
    except Exception:
        db.session.rollback()
        checks.append(_check("Tietokanta", "Migraatiot", INFO, "Versiotaulua ei löydy (testikanta)."))
    return checks


def summary(checks: list[dict]) -> dict:
    return {status: sum(1 for c in checks if c["status"] == status) for status in (OK, WARN, FAIL, INFO)}
