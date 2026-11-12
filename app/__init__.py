from flask import Flask, redirect, url_for
from flask_login import current_user

from app.config import apply_testing_defaults, get_config
from app.core.diagnostics import configure_stdout_logging
from app.core.errors import register_error_handlers
from app.extensions import cache, csrf, db, limiter, login_manager, mail, migrate


def create_app(config_object=None):
    app = Flask(__name__)
    config = config_object or get_config()
    app.config.from_object(config)
    apply_testing_defaults(app)
    configure_stdout_logging(app)
    _init_sentry(app)
    _apply_proxy_fix(app)

    if app.config.get("REDIS_URL"):
        app.config["CACHE_TYPE"] = "RedisCache"
        app.config["CACHE_REDIS_URL"] = app.config["REDIS_URL"]
        app.config["RATELIMIT_STORAGE_URI"] = app.config["REDIS_URL"]
        _configure_redis_fallback(app)

    if not app.config.get("SECRET_KEY"):
        if app.config.get("TESTING"):
            app.config["SECRET_KEY"] = "test-secret-key"
        else:
            raise RuntimeError("SECRET_KEY environment variable is required.")

    if not app.config.get("SQLALCHEMY_DATABASE_URI") and not app.config.get("TESTING"):
        raise RuntimeError("DATABASE_URL environment variable is required.")

    _init_extensions(app)
    from app.listings.housing import install_listeners

    install_listeners()  # keeps Listing.company_key in step with the name / Y-tunnus
    _register_blueprints(app)
    register_error_handlers(app)
    _register_security_headers(app)
    _register_cli(app)
    _register_root_routes(app)
    _init_ai_enrichment(app)
    _init_scheduler(app)
    _log_db_health(app)

    return app


def _apply_proxy_fix(app):
    """Trust Render's single reverse-proxy hop for X-Forwarded-* headers.

    Without this, request.remote_addr is Render's proxy IP for every
    request, which breaks per-client login rate limiting.
    """
    if app.config.get("TESTING"):
        return
    from werkzeug.middleware.proxy_fix import ProxyFix

    hops = app.config.get("PROXY_FIX_X_FOR", 1)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)


def _register_security_headers(app):
    """Security headers on every response. Render serves the app directly, so
    deploy/nginx.conf (which sets the same headers) is not in front of it."""

    @app.after_request
    def add_security_headers(response):
        from flask import request

        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # The web form embed page is meant to sit in an iframe on customer sites.
        if request.endpoint != "forms.iframe_embed":
            headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.is_secure:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def _init_sentry(app):
    """No-op unless SENTRY_DSN is configured — keeps Sentry optional."""
    dsn = app.config.get("SENTRY_DSN")
    if not dsn:
        return
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=app.config.get("SENTRY_TRACES_SAMPLE_RATE", 0.0),
    )


def _configure_redis_fallback(app):
    """Fall back to in-memory cache/rate limits when Redis is unreachable."""
    redis_url = app.config.get("REDIS_URL")
    if not redis_url:
        return
    try:
        import redis

        client = redis.from_url(redis_url, socket_connect_timeout=2)
        client.ping()
    except Exception as exc:
        app.logger.warning(
            "REDIS_URL unreachable (%s); using in-memory cache and rate-limit storage",
            exc,
        )
        app.config["CACHE_TYPE"] = "SimpleCache"
        app.config.pop("CACHE_REDIS_URL", None)
        app.config["RATELIMIT_STORAGE_URI"] = "memory://"


def _log_db_health(app):
    if app.config.get("TESTING"):
        return
    from app.core.db_health import log_migration_status

    log_migration_status(app)


def _init_scheduler(app):
    """Bind scheduler jobs to this app (executed via `flask run-scheduler`)."""
    if app.config.get("TESTING"):
        return
    from app.scheduler import configure_scheduler_app

    configure_scheduler_app(app)


def _init_ai_enrichment(app):
    from app.ai.queue import init_enrichment_queue

    init_enrichment_queue(app)


def _init_extensions(app):
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    mail.init_app(app)
    app.config.setdefault("RATELIMIT_STORAGE_URI", app.config.get("RATELIMIT_STORAGE_URI", "memory://"))
    limiter.init_app(app)
    cache.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    from app.users.models import User

    @login_manager.user_loader
    def load_user(user_id):
        user = db.session.get(User, int(user_id))
        # a deactivated organization ends its users' open sessions too
        if user is not None and user.organization is not None and not user.organization.is_active:
            return None
        return user

    with app.app_context():
        from app.admin import models as admin_models  # noqa: F401
        from app.api import models as api_models  # noqa: F401
        from app.auth import models as auth_models  # noqa: F401
        from app.email import models as email_models  # noqa: F401
        from app.leads import models as leads_models  # noqa: F401
        from app.companies import models as companies_models  # noqa: F401
        from app.tasks import models as tasks_models  # noqa: F401
        from app.users import models as user_models  # noqa: F401
        from app.custom_fields import models as custom_fields_models  # noqa: F401
        from app.segments import models as segments_models  # noqa: F401
        from app.sequences import models as sequences_models  # noqa: F401
        from app.automations import models as automations_models  # noqa: F401
        from app.notifications import models as notifications_models  # noqa: F401
        from app.gdpr import models as gdpr_models  # noqa: F401
        from app.calendar import models as calendar_models  # noqa: F401
        from app.proposals import models as proposals_models  # noqa: F401
        from app.analytics import models as analytics_models  # noqa: F401
        from app.webhooks import models as webhooks_models  # noqa: F401
        from app.forms import models as forms_models  # noqa: F401
        from app.streams import models as streams_models  # noqa: F401
        from app.search import models as search_models  # noqa: F401
        from app.listings import models as listings_models  # noqa: F401


def _register_blueprints(app):
    from app.admin.invite_routes import invite_bp
    from app.admin.routes import admin_bp
    from app.analytics import analytics_bp
    from app.api import api_bp, web_api_bp
    from app.auth.routes import auth_bp
    from app.backups import backups_bp
    from app.companies import companies_bp, contacts_bp
    import app.companies.routes as companies_routes  # noqa: F401
    from app.email import email_bp, webhooks_bp
    from app.leads.routes import leads_bp
    from app.settings import settings_bp
    from app.tasks import tasks_bp
    from app.sequences import sequences_bp
    from app.automations import automations_bp
    from app.notifications import notifications_bp
    from app.calendar.routes import calendar_bp
    from app.proposals import proposals_bp, proposals_public_bp
    from app.forms import forms_bp, forms_public_api_bp
    from app.search.n8n_routes import n8n_bp
    from app.listings.routes import listings_n8n_bp
    from app.listings.target_routes import targets_bp
    from app.listings.insight_routes import insights_bp
    from app.integrations.routes import integrations_bp
    from app.billing.routes import billing_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(invite_bp)
    app.register_blueprint(backups_bp, url_prefix="/admin")
    app.register_blueprint(leads_bp)
    app.register_blueprint(companies_bp)
    app.register_blueprint(contacts_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(sequences_bp)
    app.register_blueprint(email_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(web_api_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(automations_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(proposals_bp)
    app.register_blueprint(proposals_public_bp)
    app.register_blueprint(forms_bp)
    app.register_blueprint(forms_public_api_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(n8n_bp)
    app.register_blueprint(listings_n8n_bp)
    app.register_blueprint(targets_bp)
    app.register_blueprint(insights_bp)
    app.register_blueprint(integrations_bp)
    app.register_blueprint(billing_bp)
    csrf.exempt(api_bp)
    csrf.exempt(webhooks_bp)
    csrf.exempt(notifications_bp)
    csrf.exempt(proposals_public_bp)
    csrf.exempt(forms_public_api_bp)
    csrf.exempt(n8n_bp)
    csrf.exempt(listings_n8n_bp)


# Display labels shared by every lead view (dashboard, list, pipeline, detail)
# so the same lead reads the same everywhere. Presentation only: stored stage
# names and source codes are untouched.
STAGE_LABELS_FI = {
    "new lead": "Uusi liidi",
    "uusi liidi": "Uusi liidi",
    "uusi": "Uusi liidi",
    "contacted": "Kontaktoitu",
    "kontaktoitu": "Kontaktoitu",
    "interested": "Kiinnostunut",
    "kiinnostunut": "Kiinnostunut",
    "kvalifioitu": "Kvalifioitu",
    "qualified": "Kvalifioitu",
    "proposal sent": "Tarjous lähetetty",
    "tarjous lähetetty": "Tarjous lähetetty",
    "proposal": "Tarjous lähetetty",
    "tarjous": "Tarjous lähetetty",
    "won": "Voitettu",
    "voitettu": "Voitettu",
    "closed won": "Voitettu",
    "lost": "Hävitty",
    "hävitty": "Hävitty",
    "closed lost": "Hävitty",
}

# "n8n" is how a lead arrived, not where it came from, so the sales views call
# it Automaatio; technical detail stays in the integration settings.
SOURCE_LABELS_FI = {
    "manual": "Lisätty käsin",
    "n8n": "Automaatio",
    "import": "Tuonti",
    "webform": "Lomake",
    "oikotie": "Oikotie",
}


ACTIVITY_LABELS_FI = {
    "note": "Muistiinpano",
    "email_sent": "Sähköposti lähetetty",
    "email_opened": "Sähköposti avattu",
    "email_clicked": "Sähköpostin linkkiä klikattu",
    "email_reply_received": "Vastaus saatu",
    "call": "Puhelu",
    "stage_changed": "Vaihe vaihdettu",
    "ai_enriched": "AI-rikastus",
    "created": "Liidi luotu",
    "updated": "Tiedot päivitetty",
    "archived": "Arkistoitu",
    "assigned": "Vastuuhenkilö vaihdettu",
    "task_created": "Tehtävä luotu",
    "task_completed": "Tehtävä tehty",
    "task_reminder_sent": "Tehtävämuistutus lähetetty",
    "sequence_enrolled": "Lisätty sekvenssiin",
    "sequence_email_sent": "Sekvenssiviesti lähetetty",
    "sequence_unenrolled": "Poistettu sekvenssistä",
    "meeting_scheduled": "Tapaaminen sovittu",
    "meeting_cancelled": "Tapaaminen peruttu",
    "proposal_sent": "Tarjous lähetetty",
    "proposal_viewed": "Tarjous avattu",
    "proposal_accepted": "Tarjous hyväksytty",
    "proposal_declined": "Tarjous hylätty",
}

LEAD_STATUS_LABELS_FI = {
    "active": "Avoin",
    "won": "Voitettu",
    "lost": "Hävitty",
    "archived": "Arkistoitu",
}


def stage_label_fi(name) -> str:
    if not name:
        return "—"
    return STAGE_LABELS_FI.get(str(name).strip().lower(), str(name))


def source_label_fi(code) -> str:
    if not code:
        return "—"
    return SOURCE_LABELS_FI.get(str(code).strip().lower(), str(code))


def to_helsinki(value):
    """UTC datetime (aware, or naive as SQLite returns it) -> Europe/Helsinki."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("Europe/Helsinki"))


def helsinki_to_utc(value):
    """A wall-clock time the user typed (naive = Finnish time) -> aware UTC."""
    from datetime import timezone
    from zoneinfo import ZoneInfo

    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("Europe/Helsinki"))
    return value.astimezone(timezone.utc)


def _nav_org_id() -> int | None:
    """The organization the navigation is showing: the user's own, or the one
    a superadmin has chosen (URL parameter, else the remembered choice)."""
    from flask_login import current_user

    if not current_user.is_authenticated:
        return None
    if current_user.is_superadmin():
        from app.core.tenant import parse_organization_id_param

        return parse_organization_id_param()
    return current_user.organization_id


def _nav_org_name() -> str | None:
    """Name of the organization a superadmin is looking at (sidebar)."""
    from flask_login import current_user

    try:
        if not current_user.is_authenticated or not current_user.is_superadmin():
            return None
        org_id = _nav_org_id()
        if not org_id:
            return None
        from app.extensions import db
        from app.users.models import Organization

        org = db.session.get(Organization, org_id)
        return org.name if org else None
    except Exception:
        return None


def _nav_overdue_count() -> int:
    """Overdue tasks of the signed-in user (sidebar badge on every page)."""
    from flask_login import current_user

    try:
        org_id = _nav_org_id()
        if not org_id or current_user.role not in ("superadmin", "admin", "user"):
            return 0
        from app.tasks.services import TaskService

        return TaskService.count_overdue(org_id, current_user.id)
    except Exception:
        from app.extensions import db

        db.session.rollback()
        return 0


def _nav_targets_count() -> int:
    """Targets waiting on the Kohteet page for the organization in view (the
    sidebar badge). Never lets a page fail."""
    from flask import request
    from flask_login import current_user

    try:
        if not current_user.is_authenticated or current_user.role not in ("superadmin", "admin", "user"):
            return 0
        org_id = _nav_org_id()
        if not org_id:
            return 0
        from app.listings.target_services import count_targets

        return count_targets(org_id)
    except Exception:
        from app.extensions import db

        db.session.rollback()
        return 0


def _register_root_routes(app):
    @app.context_processor
    def inject_ui_helpers():
        def has_endpoint(endpoint_name: str) -> bool:
            return endpoint_name in app.view_functions

        return {
            "nav_targets_count": _nav_targets_count(),
            "nav_overdue_count": _nav_overdue_count(),
            "active_org_id": _nav_org_id(),
            "active_org_name": _nav_org_name(),
            "has_endpoint": has_endpoint,
            "stage_label": stage_label_fi,
            "source_label": source_label_fi,
            "STAGE_LABELS_FI": STAGE_LABELS_FI,
            "ACTIVITY_LABELS_FI": ACTIVITY_LABELS_FI,
            "LEAD_STATUS_LABELS_FI": LEAD_STATUS_LABELS_FI,
            "SOURCE_LABELS_FI": SOURCE_LABELS_FI,
        }

    @app.template_filter("helsinki")
    def helsinki(value):
        """A stored UTC timestamp as Finnish local time, for display. Dates
        and empty values pass through untouched."""
        return to_helsinki(value)

    @app.template_filter("fi_msg")
    def fi_msg(message):
        from app.core.messages_fi import fi_message

        return fi_message(message)

    @app.template_filter("fi_percent")
    def fi_percent(value, decimals=1):
        """Render a number as Finnish-locale percent text, e.g. 0,0 %."""
        if value is None:
            return "—"
        try:
            formatted = f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return str(value)
        return formatted.replace(".", ",") + " %"

    @app.template_filter("initials")
    def initials(name, fallback="–"):
        """Return up to 2 uppercase initials from a person's full name."""
        if not name:
            return fallback
        parts = [p for p in str(name).split() if p]
        if not parts:
            return fallback
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("analytics.dashboard"))
        return redirect(url_for("auth.login"))


def _register_cli(app):
    from app.cli import register_cli

    register_cli(app)
