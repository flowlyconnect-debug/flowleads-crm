"""The sign-in side pages (2FA, password, invite, errors) are standalone
cards: never wrapped in the signed-in app shell, and in Finnish."""

import pyotp

from app.auth.models import BackupCode
from app.auth.services import enable_totp, setup_totp
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.users.models import User
from app.users.services import create_organization, create_user

# markup that only the signed-in app shell (base.html) renders
SHELL_MARKERS = ('id="app-sidebar"', 'id="sidebar-toggle"', "app-authenticated")


def _admin(app, email="admin-2fa@acme.fi"):
    with app.app_context():
        org = create_organization("Acme 2FA", "acme-2fa-" + email.split("@")[0])
        db.session.flush()
        create_user(email, "securepassword1", role="admin", organization_id=org.id)
        db.session.commit()
    return email


def _assert_standalone(page: str):
    for marker in SHELL_MARKERS:
        assert marker not in page, marker
    assert "auth-card.css" in page
    assert "Kirjaudu ulos" in page


def test_two_fa_setup_is_a_standalone_finnish_page(app, client):
    email = _admin(app)
    login = client.post("/auth/login", data={"email": email, "password": "securepassword1"})
    assert login.headers["Location"].endswith("/auth/2fa/setup")

    page = client.get("/auth/2fa/setup").get_data(as_text=True)
    _assert_standalone(page)
    assert "Ota kaksivaiheinen tunnistautuminen käyttöön" in page
    assert "data:image/png;base64," in page
    with app.app_context():
        secret = User.query.filter_by(email=email).one().totp_secret
    assert secret in page  # manual key for people who cannot scan

    wrong = client.post("/auth/2fa/setup", data={"token": "000000"}).get_data(as_text=True)
    _assert_standalone(wrong)
    assert "Koodi ei kelpaa" in wrong

    done = client.post("/auth/2fa/setup", data={"token": pyotp.TOTP(secret).now()}).get_data(as_text=True)
    _assert_standalone(done)
    assert "Tallenna varakoodit" in done
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.totp_enabled is True
        assert BackupCode.query.filter_by(user_id=user.id).count() > 0


def test_two_fa_verify_is_a_standalone_finnish_page(app, client):
    email = _admin(app, "admin-verify@acme.fi")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        secret, _uri, _qr = setup_totp(user)
        enable_totp(user, pyotp.TOTP(secret).now())

    response = client.post("/auth/login", data={"email": email, "password": "securepassword1"})
    assert response.headers["Location"].endswith("/auth/2fa/verify")
    page = client.get("/auth/2fa/verify").get_data(as_text=True)
    _assert_standalone(page)
    assert "Vahvista kirjautuminen" in page

    bad = client.post("/auth/2fa/verify", data={"token": "000000"}).get_data(as_text=True)
    _assert_standalone(bad)
    assert "Koodi ei kelpaa" in bad

    ok = client.post("/auth/2fa/verify", data={"token": pyotp.TOTP(secret).now()})
    assert ok.status_code == 302 and ok.headers["Location"].endswith("/dashboard")
    with client.session_transaction() as sess:
        assert sess.get(TWO_FA_SESSION_KEY) is True


def test_password_and_invite_pages_are_standalone(app, client):
    for path, heading in (
        ("/auth/reset-password", "Unohtuiko salasana?"),
        ("/invite/not-a-real-token", "Kutsu ei kelpaa"),
    ):
        page = client.get(path).get_data(as_text=True)
        assert heading in page
        assert "auth-card.css" in page
        for marker in SHELL_MARKERS:
            assert marker not in page


def test_error_pages_are_finnish_and_never_show_the_app_shell(app, client):
    guest = client.get("/tata-sivua-ei-ole")
    assert guest.status_code == 404
    text = guest.get_data(as_text=True)
    assert "Sivua ei löytynyt" in text and "Kirjautumiseen" in text

    with app.app_context():
        create_user("sa-errors@test.com", "securepassword1", role="superadmin").totp_enabled = True
        db.session.commit()
    client.post("/auth/login", data={"email": "sa-errors@test.com", "password": "securepassword1"})
    with client.session_transaction() as sess:
        sess[TWO_FA_SESSION_KEY] = True
    signed_in = client.get("/tata-sivua-ei-ole")
    assert signed_in.status_code == 404
    text = signed_in.get_data(as_text=True)
    assert "Sivua ei löytynyt" in text and "Etusivulle" in text
    for marker in SHELL_MARKERS:
        assert marker not in text


# --- mandatory 2FA applies to every page -------------------------------------


def test_production_config_enforces_2fa_on_all_pages():
    from app.config import Config, ProductionConfig

    assert Config.TWO_FA_ENFORCE_ALL_PAGES is True
    assert ProductionConfig.TWO_FA_ENFORCE_ALL_PAGES is True


def test_admin_cannot_reach_the_app_before_setting_up_2fa(app, client):
    app.config["TWO_FA_ENFORCE_ALL_PAGES"] = True
    email = _admin(app, "admin-skip-setup@acme.fi")
    client.post("/auth/login", data={"email": email, "password": "securepassword1"})

    for path in ("/leads/", "/dashboard", "/settings/search-profiles", "/tasks/"):
        response = client.get(path)
        assert response.status_code == 302, path
        assert response.headers["Location"].endswith("/auth/2fa/setup"), path

    api = client.get("/api/notifications")
    assert api.status_code == 403
    assert api.get_json()["code"] == "two_factor_required"
    write = client.post("/leads/new", data={"email": "x@y.fi"})
    assert write.status_code == 403

    # the auth pages themselves stay reachable
    assert client.get("/auth/2fa/setup").status_code == 200
    assert client.get("/auth/logout").status_code == 302


def test_admin_with_2fa_must_verify_before_every_page_and_then_gets_in(app, client):
    app.config["TWO_FA_ENFORCE_ALL_PAGES"] = True
    email = _admin(app, "admin-skip-verify@acme.fi")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        secret, _uri, _qr = setup_totp(user)
        enable_totp(user, pyotp.TOTP(secret).now())

    client.post("/auth/login", data={"email": email, "password": "securepassword1"})
    blocked = client.get("/leads/")
    assert blocked.status_code == 302
    assert blocked.headers["Location"].endswith("/auth/2fa/verify")

    client.post("/auth/2fa/verify", data={"token": pyotp.TOTP(secret).now()})
    assert client.get("/leads/").status_code == 200


def test_plain_users_are_not_asked_for_2fa(app, client):
    app.config["TWO_FA_ENFORCE_ALL_PAGES"] = True
    with app.app_context():
        org = create_organization("Acme Users", "acme-users")
        db.session.flush()
        create_user("seller@acme.fi", "securepassword1", role="user", organization_id=org.id)
        db.session.commit()
    client.post("/auth/login", data={"email": "seller@acme.fi", "password": "securepassword1"})
    assert client.get("/leads/").status_code == 200


def test_backup_code_signs_in_once_through_the_verify_page(app, client):
    app.config["TWO_FA_ENFORCE_ALL_PAGES"] = True
    email = _admin(app, "admin-backup@acme.fi")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        secret, _uri, _qr = setup_totp(user)
        codes = enable_totp(user, pyotp.TOTP(secret).now())
        total = len(codes)

    client.post("/auth/login", data={"email": email, "password": "securepassword1"})
    assert "varakood" in client.get("/auth/2fa/verify").get_data(as_text=True)

    bad = client.post("/auth/2fa/verify", data={"token": "AAAA-BBBB-CCCC"})
    assert bad.status_code == 200 and "Koodi ei kelpaa" in bad.get_data(as_text=True)
    assert client.get("/leads/").status_code == 302  # still locked out

    ok = client.post("/auth/2fa/verify", data={"token": codes[0]}, follow_redirects=True)
    assert ok.status_code == 200
    assert f"jäljellä {total - 1}" in ok.get_data(as_text=True)
    assert client.get("/leads/").status_code == 200

    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert BackupCode.query.filter_by(user_id=user.id, used=True).count() == 1
        from app.users.models import AuditLog

        assert AuditLog.query.filter_by(action="2fa_backup_code_used", user_id=user.id).count() == 1

        again = app.test_client()
        again.post("/auth/login", data={"email": email, "password": "securepassword1"})
        reused = again.post("/auth/2fa/verify", data={"token": codes[0]})
        assert reused.status_code == 200
        assert "Koodi ei kelpaa" in reused.get_data(as_text=True)
