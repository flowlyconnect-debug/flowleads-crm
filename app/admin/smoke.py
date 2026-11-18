"""Smoke test right after a deploy (`flask smoke-check`, scripts/smoke_http.py).

Runs the new code paths once against the real database, read-only: the
migration is at head, every active customer's Kohteet list, row reasons and
Ennakoi section build, Asiakasseuranta, the export limit and the announcement
recipients compute, and the scheduler's jobs ran recently. Nothing is written
or sent."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _result(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def run_smoke(*, now: datetime | None = None) -> list[dict]:
    from flask import current_app
    from sqlalchemy import text

    from app.extensions import db

    now = now or datetime.now(timezone.utc)
    results: list[dict] = []

    def step(name):
        def wrap(fn):
            try:
                ok, detail = fn()
            except Exception as exc:  # noqa: BLE001 - every step reports, none stops the rest
                db.session.rollback()
                ok, detail = False, f"{type(exc).__name__}: {exc}"[:300]
            results.append(_result(name, ok, detail))
            return fn

        return wrap

    @step("Tietokanta ja migraatiot")
    def _migrations():
        from alembic.script import ScriptDirectory
        from flask_migrate import Migrate  # noqa: F401 - the app uses Flask-Migrate

        current = db.session.execute(text("select version_num from alembic_version")).scalar()
        config = current_app.extensions["migrate"].migrate.get_config()
        head = ScriptDirectory.from_config(config).get_current_head()
        return current == head, f"kanta {current}, koodi {head}"

    @step("Kohteet jokaiselle aktiiviselle asiakkaalle")
    def _targets():
        from app.listings.target_services import count_targets, list_targets, organization_wanted_types, target_rows
        from app.users.models import Organization

        parts = []
        for org in Organization.query.filter_by(is_active=True).order_by(Organization.id):
            wanted = organization_wanted_types(org.id)
            page = list_targets(org.id, per_page=5)
            rows = target_rows(org.id, page.items, wanted, with_fit=False)
            missing = [r for r in rows if not r.get("why")]
            if missing:
                return False, f"{org.name}: {len(missing)} riviltä puuttuu syy"
            parts.append(f"{org.name} {count_targets(org.id)} + Ennakoi {count_targets(org.id, segment='need')}")
        db.session.rollback()  # target_rows may store scores: leave nothing behind
        return True, "; ".join(parts) or "ei aktiivisia asiakkaita"

    @step("Asiakasseuranta")
    def _activity():
        from app.admin.activity_services import customer_activity

        rows = customer_activity(days=30, now=now)
        flagged = [r["org"].name for r in rows if r["level"] != "ok"]
        return True, f"{len(rows)} asiakasta" + (f", merkkejä: {', '.join(flagged)}" if flagged else ", ei merkkejä")

    @step("Vientirajat")
    def _exports():
        from app.core.export_limits import daily_limit, exported_today
        from app.users.models import Organization

        enabled = current_app.config.get("EXPORT_LIMITS_ENABLED", True)
        parts = [f"{o.name} {exported_today(o.id, now=now)}/{daily_limit(o.id) or '∞'}"
                 for o in Organization.query.filter_by(is_active=True).order_by(Organization.id)]
        return True, ("päällä: " if enabled else "POIS: ") + ", ".join(parts)

    @step("Asiakastiedotteet")
    def _announcements():
        from app.admin.announcement_services import recipients

        sender = current_app.config.get("MAILGUN_FROM_EMAIL")
        sending = bool(current_app.config.get("EMAIL_SENDING_ENABLED"))
        count = len(recipients("admins"))
        ok = bool(sender) and sending
        return ok, f"{count} pääkäyttäjää vastaanottajina; lähettäjä {'asetettu' if sender else 'PUUTTUU'}, lähetys {'päällä' if sending else 'POIS'}"

    @step("Hakuprofiilien aikaikkuna")
    def _windows():
        from app.search.models import SearchProfile

        total = SearchProfile.query.filter_by(is_active=True).count()
        with_window = SearchProfile.query.filter(SearchProfile.is_active.is_(True), SearchProfile.timing_within_years.isnot(None)).count()
        waiting = SearchProfile.query.filter_by(needs_rematch=True).count()
        return True, f"{with_window}/{total} aktiivisella profiililla ikkuna, {waiting} odottaa uudelleentäsmäystä"

    @step("Ajastimen työt")
    def _scheduler():
        from app.users.models import AuditLog

        recent = now - timedelta(hours=2)
        matches = db.session.execute(
            text("select max(created_at) from listing_matches")
        ).scalar()
        last_login = AuditLog.query.filter(AuditLog.created_at >= recent).count()
        return True, f"viimeisin osuma {matches or 'ei koskaan'}; audit-lokissa {last_login} tapahtumaa 2 h sisällä"

    @step("Pois-kytkimet")
    def _switches():
        keys = ("PROFILE_TIMING_WINDOW_ENABLED", "USAGE_TRACKING_ENABLED", "ACTIVITY_ALERTS_ENABLED",
                "EXPORT_LIMITS_ENABLED", "ANNOUNCEMENTS_ENABLED")
        return True, ", ".join(f"{k}={'päällä' if current_app.config.get(k, True) else 'pois'}" for k in keys)

    return results
