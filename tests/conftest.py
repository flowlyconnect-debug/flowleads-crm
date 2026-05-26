import os

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db


@pytest.fixture
def app():
    os.environ["FLASK_ENV"] = "testing"
    application = create_app(TestingConfig)
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
