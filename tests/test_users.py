import pytest

from app.extensions import db
from app.users.services import UserServiceError, create_organization, create_user


@pytest.fixture
def organization(app):
    with app.app_context():
        org = create_organization("Acme Corp", "acme-corp")
        db.session.commit()
        return org.id


def test_create_user_success(app, organization):
    with app.app_context():
        user = create_user(
            "user@acme.com",
            "securepassword1",
            role="user",
            organization_id=organization,
        )
        db.session.commit()
        assert user.id is not None
        assert user.email == "user@acme.com"


def test_create_user_duplicate_email_fails(app, organization):
    with app.app_context():
        create_user("dup@acme.com", "securepassword1", role="user", organization_id=organization)
        db.session.commit()
        with pytest.raises(UserServiceError) as exc:
            create_user("dup@acme.com", "anotherpassword1", role="user", organization_id=organization)
        assert exc.value.code == "duplicate_email"


def test_create_user_invalid_role_fails(app, organization):
    with app.app_context():
        with pytest.raises(UserServiceError) as exc:
            create_user(
                "bad@acme.com",
                "securepassword1",
                role="invalid",
                organization_id=organization,
            )
        assert exc.value.code == "invalid_role"
