import os


def _env_or_default(key: str, default: str) -> str:
    """Treat unset or blank env vars as missing (Docker .env often has KEY=)."""
    value = os.environ.get(key)
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Render Postgres may close idle SSL connections; pre-ping avoids stale pool errors.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") == "production"

    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None

    MAIL_SERVER = "smtp.mailgun.org"
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get("MAILGUN_FROM_EMAIL")
    MAIL_PASSWORD = os.environ.get("MAILGUN_API_KEY")
    MAIL_DEFAULT_SENDER = (
        os.environ.get("MAILGUN_FROM_NAME", "FlowLeads"),
        os.environ.get("MAILGUN_FROM_EMAIL"),
    )

    MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY")
    MAILGUN_DOMAIN = os.environ.get("MAILGUN_DOMAIN")
    MAILGUN_FROM_EMAIL = os.environ.get("MAILGUN_FROM_EMAIL")
    MAILGUN_FROM_NAME = os.environ.get("MAILGUN_FROM_NAME", "FlowLeads")
    MAILGUN_WEBHOOK_SIGNING_KEY = os.environ.get("MAILGUN_WEBHOOK_SIGNING_KEY")
    EMAIL_SENDING_ENABLED = os.environ.get("EMAIL_SENDING_ENABLED", "true").lower() == "true"

    EMAIL_MAX_BODY_CHARS = 100_000
    EMAIL_BODY_PREVIEW_CHARS = 300

    BACKUP_DIR = os.environ.get("BACKUP_DIR", "./backups")
    BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", 30))
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "./uploads")

    REDIS_URL = os.environ.get("REDIS_URL")
    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "memory://")

    LOGIN_RATE_LIMIT = os.environ.get("LOGIN_RATE_LIMIT", "5/minute")
    API_RATE_LIMIT = os.environ.get("API_RATE_LIMIT", "100/hour")
    FORM_SUBMISSION_RATE_LIMIT = os.environ.get("FORM_SUBMISSION_RATE_LIMIT", "10/hour")

    PASSWORD_RESET_SALT = os.environ.get("PASSWORD_RESET_SALT", "password-reset-salt")
    PASSWORD_RESET_MAX_AGE = int(os.environ.get("PASSWORD_RESET_MAX_AGE", 3600))

    MAX_FAILED_LOGIN_ATTEMPTS = 5
    LOGIN_LOCKOUT_MINUTES = 15
    PASSWORD_MIN_LENGTH = 12

    PUBLIC_REGISTRATION_ENABLED = os.environ.get("PUBLIC_REGISTRATION_ENABLED", "false").lower() == "true"

    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    AI_ENRICHMENT_ENABLED = os.environ.get("AI_ENRICHMENT_ENABLED", "true").lower() == "true"
    AI_ENRICHMENT_MODEL = os.environ.get("AI_ENRICHMENT_MODEL", "gpt-4o-mini")
    AI_PREDICTION_MODEL = os.environ.get("AI_PREDICTION_MODEL") or os.environ.get(
        "AI_ENRICHMENT_MODEL", "gpt-4o-mini"
    )
    AI_AUTO_ENRICH_ON_CREATE = os.environ.get("AI_AUTO_ENRICH_ON_CREATE", "true").lower() == "true"
    AI_MAX_CONCURRENT_ENRICHMENTS = int(os.environ.get("AI_MAX_CONCURRENT_ENRICHMENTS", 3))
    AI_ENRICHMENT_MAX_RETRIES = int(os.environ.get("AI_ENRICHMENT_MAX_RETRIES", 2))
    AI_ENRICHMENT_RETRY_DELAY_SECONDS = int(os.environ.get("AI_ENRICHMENT_RETRY_DELAY_SECONDS", 60))
    AI_TOKEN_COST_PER_1K = float(os.environ.get("AI_TOKEN_COST_PER_1K", "0.00015"))

    CACHE_TYPE = "SimpleCache"
    DASHBOARD_CACHE_SECONDS = int(os.environ.get("DASHBOARD_CACHE_SECONDS", 300))

    CALENDAR_ENCRYPTION_KEY = os.environ.get("CALENDAR_ENCRYPTION_KEY")
    WEBHOOK_ENCRYPTION_KEY = os.environ.get("WEBHOOK_ENCRYPTION_KEY")
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
    GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI")
    MICROSOFT_CLIENT_ID = os.environ.get("MICROSOFT_CLIENT_ID")
    MICROSOFT_CLIENT_SECRET = os.environ.get("MICROSOFT_CLIENT_SECRET")
    MICROSOFT_REDIRECT_URI = os.environ.get("MICROSOFT_REDIRECT_URI")

    DEBUG_DIAGNOSTICS = os.environ.get("DEBUG_DIAGNOSTICS", "false").lower() == "true"

    N8N_MASTER_SECRET = os.environ.get("N8N_MASTER_SECRET")
    APP_BASE_URL = os.environ.get("APP_BASE_URL")

    ENABLE_TEST_JOBS = os.environ.get("ENABLE_TEST_JOBS", "false").lower() == "true"


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True


def _testing_calendar_fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


def _testing_webhook_fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    _CALENDAR_TEST_FERNET_KEY = _testing_calendar_fernet_key()
    CALENDAR_ENCRYPTION_KEY = _CALENDAR_TEST_FERNET_KEY
    _WEBHOOK_TEST_FERNET_KEY = _testing_webhook_fernet_key()
    WEBHOOK_ENCRYPTION_KEY = _WEBHOOK_TEST_FERNET_KEY
    SERVER_NAME = "localhost"
    PREFERRED_URL_SCHEME = "http"
    WTF_CSRF_ENABLED = False
    SESSION_PROTECTION = None
    LOGIN_RATE_LIMIT = "1000/minute"
    API_RATE_LIMIT = _env_or_default("TEST_API_RATE_LIMIT", "1000/hour")
    FORM_SUBMISSION_RATE_LIMIT = _env_or_default("TEST_FORM_SUBMISSION_RATE_LIMIT", "1000/hour")
    SQLALCHEMY_DATABASE_URI = _env_or_default("TEST_DATABASE_URL", "sqlite://")
    AI_ENRICHMENT_ENABLED = _env_or_default("AI_ENRICHMENT_ENABLED", "false").lower() == "true"
    AI_AUTO_ENRICH_ON_CREATE = _env_or_default("AI_AUTO_ENRICH_ON_CREATE", "false").lower() == "true"
    EMAIL_SENDING_ENABLED = _env_or_default("EMAIL_SENDING_ENABLED", "true").lower() == "true"
    MAILGUN_FROM_EMAIL = _env_or_default("MAILGUN_FROM_EMAIL", "test@example.com")
    MAILGUN_FROM_NAME = _env_or_default("MAILGUN_FROM_NAME", "FlowLeads Test")
    MAILGUN_API_KEY = _env_or_default("MAILGUN_API_KEY", "test-key")
    MAILGUN_DOMAIN = _env_or_default("MAILGUN_DOMAIN", "example.com")
    MAILGUN_WEBHOOK_SIGNING_KEY = _env_or_default("MAILGUN_WEBHOOK_SIGNING_KEY", "test-webhook-key")
    N8N_MASTER_SECRET = _env_or_default("N8N_MASTER_SECRET", "test-n8n-secret")
    APP_BASE_URL = _env_or_default("APP_BASE_URL", "http://localhost")
    MAIL_USERNAME = MAILGUN_FROM_EMAIL
    MAIL_PASSWORD = MAILGUN_API_KEY
    MAIL_DEFAULT_SENDER = (MAILGUN_FROM_NAME, MAILGUN_FROM_EMAIL)


TESTING_EMAIL_DEFAULTS = {
    "MAILGUN_FROM_EMAIL": "test@example.com",
    "MAILGUN_FROM_NAME": "FlowLeads Test",
    "MAILGUN_API_KEY": "test-key",
    "MAILGUN_DOMAIN": "example.com",
    "MAILGUN_WEBHOOK_SIGNING_KEY": "test-webhook-key",
}


def apply_testing_defaults(app) -> None:
    """Ensure blank .env Mailgun values do not break pytest (incl. Docker)."""
    if not app.config.get("TESTING"):
        return
    for key, default in TESTING_EMAIL_DEFAULTS.items():
        value = app.config.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            app.config[key] = default
    app.config["MAIL_USERNAME"] = app.config["MAILGUN_FROM_EMAIL"]
    app.config["MAIL_PASSWORD"] = app.config["MAILGUN_API_KEY"]
    app.config["MAIL_DEFAULT_SENDER"] = (
        app.config.get("MAILGUN_FROM_NAME") or "FlowLeads Test",
        app.config["MAILGUN_FROM_EMAIL"],
    )


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config():
    env = os.environ.get("FLASK_ENV", "development")
    return config_by_name.get(env, DevelopmentConfig)
