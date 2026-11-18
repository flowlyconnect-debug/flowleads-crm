"""End-to-end browser test: a real Chromium against a live local CRM, through
the paths a customer, a customer admin and a superadmin take. Real sign-in
with two-factor codes. Skipped when Playwright is not installed (CI installs
it: .github/workflows/tests.yml).

    pip install playwright==1.63.0 && python -m playwright install chromium
    python -m pytest tests/test_e2e_browser.py
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
pyotp = pytest.importorskip("pyotp")

from werkzeug.serving import make_server  # noqa: E402

PASSWORD = "securepassword1"
NOW = datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from app import create_app
    from app.config import TESTING_EMAIL_DEFAULTS, TestingConfig, _env_or_default, apply_testing_defaults
    from app.extensions import db

    os.environ["FLASK_ENV"] = "testing"
    for key, default in TESTING_EMAIL_DEFAULTS.items():
        os.environ[key] = _env_or_default(key, default)
    path = tmp_path_factory.mktemp("e2e") / "e2e.db"

    class E2EConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"
        SERVER_NAME = None
        TWO_FA_ENFORCE_ALL_PAGES = True  # as in production
        TARGETS_NEED_LIMIT = 0  # the Ennakoi section, as in production

    app = create_app(E2EConfig)
    apply_testing_defaults(app)
    app.config["SERVER_NAME"] = None
    secrets = {}
    with app.app_context():
        db.create_all()
        secrets = _seed(db)

    sent = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        return True, "<e2e@test>", None

    import app.email.services as email_services

    original = email_services._mailgun_send
    email_services._mailgun_send = fake_send
    server = make_server("127.0.0.1", 0, app, threaded=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"app": app, "url": f"http://127.0.0.1:{server.server_port}", "secrets": secrets, "sent": sent}
    finally:
        server.shutdown()
        email_services._mailgun_send = original
        with app.app_context():
            db.session.remove()
            db.drop_all()


def _seed(db):
    from app.listings import services as svc
    from app.listings.models import Listing, ListingRenovation
    from app.search.models import SearchProfile
    from app.users.services import create_organization, create_user

    org = create_organization("Testiurakointi Oy", "testiurakointi")
    db.session.flush()
    secrets = {}
    for email, role, org_id in (
        ("admin@testiurakointi.fi", "admin", org.id),
        ("myyja@testiurakointi.fi", "user", org.id),
        ("root@flowleads-e2e.fi", "superadmin", None),
    ):
        user = create_user(email, PASSWORD, role=role, organization_id=org_id)
        user.totp_secret = pyotp.random_base32()
        user.totp_enabled = True
        secrets[email] = user.totp_secret
    db.session.add(SearchProfile(organization_id=org.id, name="Putket", remonttityyppi="Putkiremontti",
                                 regions=["Koko Suomi"], source="oikotie", is_active=True, min_confidence=50,
                                 needs_rematch=False, timing_within_years=2))
    db.session.commit()
    targets = (
        ("As Oy Päätetty", "Yhtiökokous päätti putkiremontista, työt alkavat 2027.", NOW.year + 1),
        ("As Oy Suunniteltu", "PTS:ssä linjasaneeraus.", NOW.year + 2),
        ("As Oy Tarve", "Putkiremontti edessä lähivuosina.", None),
    )
    for n, (name, evidence, year) in enumerate(targets):
        listing = Listing(source="import", external_id=f"e2e-{n}", maakunta="Pirkanmaa", kaupunki="Tampere",
                          osoite=f"Testikatu {n + 1}", taloyhtio=name, analysis_status="done", first_seen_at=NOW,
                          last_seen_at=NOW, published_at=NOW - timedelta(days=2), isannoitsija="Isännöinti Oy",
                          isannoitsija_henkilo="Iida Isännöitsijä", isannoitsija_puh="040 123 4567",
                          isannoitsija_email=f"iida{n}@isannointi.example", build_year=1972, building_type="Kerrostalo")
        db.session.add(listing)
        db.session.flush()
        db.session.add(ListingRenovation(listing_id=listing.id, remonttityyppi="Putkiremontti", confidence=85,
                                         evidence=evidence, timing_year=year, timing=str(year) if year else None))
        db.session.flush()
        svc.match_listing(listing, now=NOW)
        db.session.commit()
    return secrets


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        executable = os.environ.get("E2E_CHROMIUM") or None
        try:
            instance = p.chromium.launch(executable_path=executable)
        except Exception as exc:  # noqa: BLE001 - no browser installed
            pytest.skip(f"Chromium not available: {exc}")
        yield instance
        instance.close()


def _signed_in(browser, live, email, **context_args):
    context = browser.new_context(**context_args)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(live["url"] + "/auth/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_load_state()
    if "/2fa/verify" in page.url:  # roles with mandatory two-factor sign-in
        page.fill("input[name=token]", pyotp.TOTP(live["secrets"][email]).now())
        page.press("input[name=token]", "Enter")
        page.wait_for_load_state()
    assert "/2fa/" not in page.url, f"two-factor sign-in failed for {email}"
    return page, errors


def test_seller_works_the_targets(browser, live):
    page, errors = _signed_in(browser, live, "myyja@testiurakointi.fi")
    page.goto(live["url"] + "/targets")
    header = page.inner_text(".leads-header__sub")
    assert "2 taloyhtiötä sopii profiiliisi." in header and "putkiremontti" in header
    rows = page.locator(".targets-list .target-row")
    assert rows.count() == 2
    assert "Putkiremontti päätetty, alkaa" in page.inner_text(".targets-list")
    # the need waits in the closed Ennakoi section
    ahead = page.locator("details.targets-ahead")
    assert not ahead.evaluate("el => el.open")
    ahead.locator("summary").click()
    assert "As Oy Tarve" in ahead.inner_text()
    # the extra filters are one click away
    page.locator("details.targets-more summary").click()
    assert page.locator("#targets-ask-input").is_visible()
    # move the decided one to leads and onto the call list
    row = rows.filter(has_text="As Oy Päätetty")
    page.check("input[name=call_list]")
    row.locator("input.target-row__check").check()
    page.click("[data-transfer-button]")
    page.wait_for_load_state()
    assert "Liidi luotu" in page.inner_text("body")
    assert page.locator(".targets-list .target-row").count() == 1
    # the lead and its call
    page.goto(live["url"] + "/tasks/calls")
    assert "As Oy Päätetty" in page.inner_text("body")
    # a seller is not offered exports
    page.goto(live["url"] + "/leads/")
    assert page.locator("details.leads-export-menu").count() == 0
    assert errors == []


def test_kohteet_fits_a_phone(browser, live):
    page, errors = _signed_in(browser, live, "myyja@testiurakointi.fi", viewport={"width": 390, "height": 844})
    page.goto(live["url"] + "/targets")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert errors == []


def test_admin_exports_leads(browser, live):
    page, errors = _signed_in(browser, live, "admin@testiurakointi.fi")
    page.goto(live["url"] + "/leads/")
    page.locator("details.leads-export-menu summary").click()
    with page.expect_download() as download:
        page.click("text=CSV (Excel)")
    assert download.value.suggested_filename.startswith("flowleads-leads-")
    assert errors == []


def test_superadmin_sees_activity_and_sends_a_test_announcement(browser, live):
    page, errors = _signed_in(browser, live, "root@flowleads-e2e.fi")
    page.goto(live["url"] + "/admin/activity")
    if page.locator(".activity-table").count() == 0:
        raise AssertionError(f"{page.url}: {page.inner_text('body')[:500]}")
    table = page.inner_text(".activity-table")
    assert "Testiurakointi Oy" in table
    page.click("text=Testiurakointi Oy")
    detail = page.inner_text("body")
    assert "myyja@testiurakointi.fi" in detail and "Kohteet-sivu" in detail
    assert "Vei liidejä CSV:nä" in detail  # the admin's export above
    page.goto(live["url"] + "/admin/announcements")
    page.fill("#announcement-subject", "Testi {yritys}")
    page.fill("#announcement-body", "Hei {yritys}, tämä on testi.")
    page.click("button[value=test]")
    page.wait_for_load_state()
    assert "Testiviesti lähetetty osoitteeseen root@flowleads-e2e.fi." in page.inner_text("body")
    assert live["sent"][-1]["to_email"] == "root@flowleads-e2e.fi"
    assert live["sent"][-1]["subject"] == "[Testi] Testi Esimerkki Oy"
    assert errors == []
