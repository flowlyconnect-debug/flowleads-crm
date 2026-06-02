"""Flask CLI commands."""

import sys

import click
from flask import current_app
from flask.cli import with_appcontext

from app.core.security import normalize_email, validate_email, validate_password
from app.extensions import db


def register_cli(app):
    @app.cli.command("create-superadmin")
    @click.option("--email", default=None, help="Superadmin email")
    @click.option("--password", default=None, help="Superadmin password", hide_input=True)
    def create_superadmin(email, password):
        """Create a superadmin user (prompts if options omitted)."""
        if not email:
            email = click.prompt("Email")
        if not password:
            password = click.prompt("Password", hide_input=True, confirmation_prompt=True)

        normalized = normalize_email(email)
        if not normalized or not validate_email(normalized):
            click.echo("Invalid email address.", err=True)
            sys.exit(1)

        ok, msg = validate_password(password)
        if not ok:
            click.echo(msg, err=True)
            sys.exit(1)

        from app.users.services import create_user, get_user_by_email

        if get_user_by_email(normalized):
            click.echo("Email is already registered.", err=True)
            sys.exit(1)

        user = create_user(normalized, password, role="superadmin", organization_id=None)
        db.session.commit()
        click.echo(f"Superadmin created: {user.email}")
        click.echo("Complete 2FA setup on first login.")

    @app.cli.command("create-organization")
    @click.option("--name", default=None, help="Organization display name")
    @click.option("--slug", default=None, help="URL-safe slug (lowercase, hyphens)")
    @with_appcontext
    def create_organization_cmd(name, slug):
        """Create an organization (seeds default pipeline stages and automations)."""
        if not name:
            name = click.prompt("Organization name")
        if not slug:
            slug = click.prompt("Slug (e.g. acme-corp)")

        from app.users.services import UserServiceError, create_organization

        try:
            org = create_organization(name, slug)
            db.session.commit()
        except UserServiceError as exc:
            db.session.rollback()
            click.echo(exc.message, err=True)
            sys.exit(1)

        click.echo(f"Organization created: {org.name} (id={org.id}, slug={org.slug})")

    @app.cli.command("backup-create")
    @with_appcontext
    def backup_create():
        """Create a backup archive now."""
        from app.backups.services import BackupServiceError, create_backup

        try:
            filename = create_backup(triggered_by_user_id=None)
            click.echo(f"Backup created: {filename}")
        except BackupServiceError as exc:
            click.echo(exc.message, err=True)
            sys.exit(1)

    @app.cli.command("backup-restore")
    @click.argument("filename")
    @click.option("--email", default=None, help="Superadmin email")
    @click.option("--yes", is_flag=True, help="Skip confirmation prompt")
    @with_appcontext
    def backup_restore(filename, email, yes):
        """Restore from a backup file (superadmin + password + 2FA)."""
        from app.backups.services import BackupServiceError, restore_backup
        from app.users.models import User
        from app.users.services import get_user_by_email

        if not yes:
            click.confirm(
                "This will OVERWRITE current data. Continue?",
                abort=True,
            )

        if not email:
            email = click.prompt("Superadmin email")
        normalized = normalize_email(email)
        user = get_user_by_email(normalized) if normalized else None
        if not user or not user.is_superadmin():
            click.echo("Superadmin user required.", err=True)
            sys.exit(1)

        password = click.prompt("Password", hide_input=True)
        totp_code = click.prompt("2FA code")

        try:
            restore_backup(
                filename,
                confirmed_by_user_id=user.id,
                totp_code=totp_code,
                password=password,
            )
            click.echo("Restore completed.")
        except BackupServiceError as exc:
            click.echo(exc.message, err=True)
            sys.exit(1)

    @app.cli.command("rotate-api-key")
    @click.argument("key_id", type=int)
    @with_appcontext
    def rotate_api_key_cmd(key_id):
        """Revoke an API key and create a replacement with the same org/name."""
        from app.api.services import APIKeyServiceError, rotate_api_key

        try:
            _api_key, full_key = rotate_api_key(key_id)
            db.session.commit()
            click.echo("API key rotated. New key (shown once):")
            click.echo(full_key)
        except APIKeyServiceError as exc:
            db.session.rollback()
            click.echo(exc.message, err=True)
            sys.exit(1)

    @app.cli.command("send-test-email")
    @click.argument("email")
    @with_appcontext
    def send_test_email_cmd(email):
        """Send a test email via Mailgun to the given address."""
        from app.core.security import normalize_email, validate_email
        from app.email.services import EmailServiceError, send_test_email_to_address

        normalized = normalize_email(email)
        if not normalized or not validate_email(normalized):
            click.echo("Invalid email address.", err=True)
            sys.exit(1)

        try:
            result = send_test_email_to_address(normalized)
            if result.get("success"):
                click.echo("Test email sent successfully.")
            else:
                click.echo(f"Failed: {result.get('error', 'unknown')}", err=True)
                sys.exit(1)
        except EmailServiceError as exc:
            click.echo(exc.message, err=True)
            sys.exit(1)

    @app.cli.command("run-scheduler")
    @with_appcontext
    def run_scheduler_cmd():
        """Run APScheduler for scheduled backups (blocking)."""
        from app.backups.scheduler import run_scheduler

        click.echo("Starting scheduler (backups, reminders every 15m, auto-tasks)...")
        run_scheduler(current_app._get_current_object())

    @app.cli.command("debug-lead-routes")
    @with_appcontext
    def debug_lead_routes():
        """Print registered lead/stage routes (debug 404 on stage PATCH)."""
        for rule in sorted(current_app.url_map.iter_rules(), key=lambda r: r.rule):
            rule_l = rule.rule.lower()
            if "lead" in rule_l or "stage" in rule_l:
                methods = ", ".join(sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"}))
                click.echo(f"{methods:12} {rule.rule}")

    @app.cli.command("list-streams")
    @click.argument("org_slug")
    @with_appcontext
    def list_streams(org_slug):
        """List all lead streams for an organization."""
        from app.leads.models import LeadStream
        from app.users.models import Organization

        org = Organization.query.filter_by(slug=org_slug).first()
        if not org:
            click.echo("Organization not found.", err=True)
            sys.exit(1)
        streams = LeadStream.query.filter_by(organization_id=org.id).all()
        for stream in streams:
            status = "✓" if stream.is_active else "✗"
            click.echo(
                f"{status} [{stream.priority}] {stream.name} — "
                f"source:{stream.source_match} key:{stream.segment_key} leads:{stream.lead_count}"
            )
