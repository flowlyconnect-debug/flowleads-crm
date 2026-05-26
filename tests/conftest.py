import os

import pytest

from app import create_app
from app.config import TESTING_EMAIL_DEFAULTS, TestingConfig, _env_or_default, apply_testing_defaults
from app.extensions import db


@pytest.fixture
def app():
    os.environ["FLASK_ENV"] = "testing"
    for key, default in TESTING_EMAIL_DEFAULTS.items():
        os.environ[key] = _env_or_default(key, default)
    application = create_app(TestingConfig)
    apply_testing_defaults(application)
    with application.app_context():
        db.create_all()
        from app.email.seed import seed_system_email_templates

        seed_system_email_templates()
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def runner(app):
    return app.test_cli_runner()
