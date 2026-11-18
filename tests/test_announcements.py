"""Asiakastiedotteet (app/admin/announcement_services.py): one e-mail to many
customers through Mailgun."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.admin.announcement_services import AnnouncementError, recipients, render, send_announcement
from app.admin.models import CustomerAnnouncement
from app.core.permissions import TWO_FA_SESSION_KEY
from app.extensions import db
from app.users.services import create_organization, create_user

PASSWORD = "securepassword1"


def _setup():
    first = create_organization("Ensimmäinen Oy", "ensimmainen")
    second = create_organization("Toinen Oy", "toinen")
    closed = create_organization("Suljettu Oy", "suljettu")
    db.session.flush()
    closed.is_active = False
    create_user("admin@ensimmainen.fi", PASSWORD, role="admin", organization_id=first.id)
    create_user("myyja@ensimmainen.fi", PASSWORD, role="user", organization_id=first.id)
    create_user("admin@toinen.fi", PASSWORD, role="admin", organization_id=second.id)
    off = create_user("pois@toinen.fi", PASSWORD, role="admin", organization_id=second.id)
    off.is_active = False
    create_user("admin@suljettu.fi", PASSWORD, role="admin", organization_id=closed.id)
    root = create_user("root-tiedote@test.com", PASSWORD, role="superadmin")
    root.totp_enabled = True
    db.session.commit()
    return first, second, root


def test_recipients_by_audience_and_customer(app):
    with app.app_context():
        first, second, _ = _setup()
        admins = {r.email for r in recipients("admins")}
        assert {"admin@ensimmainen.fi", "admin@toinen.fi"} <= admins
        assert not admins & {"myyja@ensimmainen.fi", "pois@toinen.fi", "admin@suljettu.fi", "root-tiedote@test.com"}
        everyone = {r.email for r in recipients("all", [first.id])}
        assert everyone == {"admin@ensimmainen.fi", "myyja@ensimmainen.fi"}
        with pytest.raises(AnnouncementError):
            recipients("nobody")


def test_render_fills_the_company_and_escapes():
    subject, body_html, body_text = render("Hei {yritys}", "Rivi <b>1</b>\n\nToinen kappale", "Ensimmäinen Oy")
    assert subject == "Hei Ensimmäinen Oy"
    assert "&lt;b&gt;1&lt;/b&gt;" in body_html and body_html.count("<p") == 3  # two paragraphs and the footer
    assert body_text.startswith("Rivi <b>1</b>") and "FlowLeadsin asiakkaana" in body_text


@patch("app.email.services._mailgun_send")
def test_send_to_chosen_customers_and_store_it(mock_send, app):
    mock_send.side_effect = lambda **kw: (False, None, "bounced") if kw["to_email"] == "admin@toinen.fi" else (True, "<id>", None)
    with app.app_context():
        first, second, root = _setup()
        announcement, outcome = send_announcement(
            subject="Päivitys {yritys}", body="Uutta.", audience="admins",
            organization_ids=[first.id, second.id], sender=root,
        )
        assert outcome == {"sent": 1, "failed": 1, "failures": [{"email": "admin@toinen.fi", "error": "bounced"}]}
        sent_to = sorted(call.kwargs["to_email"] for call in mock_send.call_args_list)
        assert sent_to == ["admin@ensimmainen.fi", "admin@toinen.fi"]
        call = next(c for c in mock_send.call_args_list if c.kwargs["to_email"] == "admin@ensimmainen.fi")
        assert call.kwargs["subject"] == "Päivitys Ensimmäinen Oy" and call.kwargs["reply_to"] == "root-tiedote@test.com"
        stored = db.session.get(CustomerAnnouncement, announcement.id)
        assert stored.recipients == 2 and stored.failed == 1


@patch("app.email.services._mailgun_send")
def test_a_test_goes_only_to_the_sender(mock_send, app):
    mock_send.return_value = (True, "<id>", None)
    with app.app_context():
        _, _, root = _setup()
        announcement, outcome = send_announcement(
            subject="Aihe", body="Teksti", audience="all", organization_ids=None, sender=root, test_only=True
        )
        assert announcement is None and outcome["sent"] == 1
        assert [c.kwargs["to_email"] for c in mock_send.call_args_list] == ["root-tiedote@test.com"]
        assert mock_send.call_args.kwargs["subject"] == "[Testi] Aihe"
        assert CustomerAnnouncement.query.count() == 0


def test_validation(app):
    with app.app_context():
        _, _, root = _setup()
        for subject, body in (("", "x"), ("x", "  ")):
            with pytest.raises(AnnouncementError):
                send_announcement(subject=subject, body=body, audience="admins", organization_ids=None, sender=root)


@patch("app.email.services._mailgun_send")
def test_announcements_page(mock_send, app, client):
    mock_send.return_value = (True, "<id>", None)
    with app.app_context():
        first, _, _ = _setup()
        first_id = first.id
    client.post("/auth/login", data={"email": "root-tiedote@test.com", "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    page = client.get("/admin/announcements").get_data(as_text=True)
    assert "Asiakastiedotteet" in page and "2 vastaanottajaa" in page
    response = client.post("/admin/announcements", data={
        "subject": "Uusi versio", "body": "Hei {yritys}", "audience": "all",
        "organization_ids": [str(first_id)], "action": "send",
    }, follow_redirects=True)
    html = response.get_data(as_text=True)
    assert "Tiedote lähetetty 2 vastaanottajalle." in html and "Uusi versio" in html
    assert mock_send.call_count == 2


def test_a_customer_admin_cannot_send(app, client):
    with app.app_context():
        _setup()
    client.post("/auth/login", data={"email": "admin@ensimmainen.fi", "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    assert client.get("/admin/announcements").status_code in (302, 403)


def test_announcements_switch(app, client):
    with app.app_context():
        _setup()
    client.post("/auth/login", data={"email": "root-tiedote@test.com", "password": PASSWORD})
    with client.session_transaction() as session:
        session[TWO_FA_SESSION_KEY] = True
    app.config["ANNOUNCEMENTS_ENABLED"] = False
    try:
        assert client.get("/admin/announcements").status_code == 404
    finally:
        app.config["ANNOUNCEMENTS_ENABLED"] = True
