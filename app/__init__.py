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
    _register_blueprints(app)
    register_error_handlers(app)
    _register_cli(app)
    _register_root_routes(app)
    _init_ai_enrichment(app)
    _init_scheduler(app)
    _log_db_health(app)

    return app


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
        return db.session.get(User, int(user_id))

    with app.app_context():
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


def _register_blueprints(app):
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(admin_bp)
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
    csrf.exempt(api_bp)
    csrf.exempt(webhooks_bp)
    csrf.exempt(notifications_bp)
    csrf.exempt(proposals_public_bp)
    csrf.exempt(forms_public_api_bp)
    csrf.exempt(n8n_bp)


def _register_root_routes(app):
    @app.context_processor
    def inject_ui_helpers():
        def has_endpoint(endpoint_name: str) -> bool:
            return endpoint_name in app.view_functions

        return {"has_endpoint": has_endpoint}

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("analytics.dashboard"))
        return redirect(url_for("auth.login"))


def _register_cli(app):
    from app.cli import register_cli

    register_cli(app)
