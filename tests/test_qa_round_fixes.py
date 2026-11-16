"""Fixes from the full-system QA round before the first customer (16.11.2026)."""

from datetime import datetime, timedelta

from app.extensions import db
from app.leads.models import Lead
from app.listings.import_services import _confidence
from app.tasks.models import Task
from tests.test_targets import _login, _org


def test_import_confidence_accepts_fractions_and_percentages():
    # 0.9 used to become 0 % and the row matched no profile
    assert _confidence("0.9") == 90
    assert _confidence("0,75") == 75
    assert _confidence("90") == 90
    assert _confidence("90 %") == 90
    assert _confidence("0.5%") == 0  # an explicit percentage stays as written
    assert _confidence("") == 100
    assert _confidence("abc") == 100
    assert _confidence("150") == 100


def _lead(org, user):
    from app.leads.models import PipelineStage

    stage = PipelineStage.query.filter_by(organization_id=org.id).order_by(PipelineStage.order_index).first()
    lead = Lead(
        organization_id=org.id,
        email="isannoitsija@example.fi",
        company="As Oy Testi",
        assigned_to=user.id,
        stage_id=stage.id,
    )
    db.session.add(lead)
    db.session.commit()
    return lead


def test_seller_can_add_a_task_from_the_lead_page(app, client):
    with app.app_context():
        org, seller = _org("qa-seller-task", role="user")
        lead = _lead(org, seller)
        lead_id, seller_id = lead.id, seller.id
        db.session.commit()
    _login(client, "user-qa-seller-task@test.com")
    due = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    # the seller's form has no assignee field
    response = client.post(
        f"/leads/{lead_id}/tasks",
        data={"title": "Soita", "type": "call", "priority": "normal", "due_date": due},
    )
    assert response.status_code == 302
    with app.app_context():
        task = Task.query.filter_by(lead_id=lead_id).one()
        assert task.assigned_to == seller_id


def test_seller_bulk_bar_offers_only_allowed_actions(app, client):
    with app.app_context():
        org, seller = _org("qa-seller-bulk", role="user")
        lead = _lead(org, seller)
        lead_id = lead.id
    _login(client, "user-qa-seller-bulk@test.com")
    html = client.get("/leads/").get_data(as_text=True)
    assert 'value="change_stage"' in html
    assert 'value="assign"' not in html
    assert 'value="archive"' not in html
    # a forbidden action from a form answers with a Finnish flash, not raw JSON
    response = client.post("/leads/bulk", data={"action": "archive", "lead_ids": str(lead_id)})
    assert response.status_code == 302
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Sinulla ei ole oikeutta" in page


def test_a_task_for_today_from_the_date_picker_is_not_overdue(app, client):
    from zoneinfo import ZoneInfo

    with app.app_context():
        org, admin = _org("qa-task-date", role="admin")
        db.session.commit()
    _login(client, "admin-qa-task-date@test.com")
    today = datetime.now(ZoneInfo("Europe/Helsinki")).date().isoformat()
    response = client.post("/tasks", json={"title": "Tänään", "due_date": today, "priority": "normal"})
    assert response.status_code == 201
    task = response.get_json()["data"]["task"]
    assert task["section"] == "today"
    assert task["overdue_days"] in (0, None)
    assert task["due_time"] == "23:59"  # Finnish time


def test_superadmin_saving_a_contact_redirects_to_the_list(app, client):
    from app.users.services import create_user
    from tests.test_targets import PASSWORD

    with app.app_context():
        org, _ = _org("qa-contact", role="admin")
        create_user("super-qa-contact@test.com", PASSWORD, role="superadmin", organization_id=None)
        org_id = org.id
        db.session.commit()
    _login(client, "super-qa-contact@test.com")
    response = client.post(
        f"/contacts/new?organization_id={org_id}",
        data={"first_name": "Maija", "last_name": "Mallikas", "email": "maija@example.fi", "company_id": "0"},
    )
    assert response.status_code == 302
    assert f"organization_id={org_id}" in response.headers["Location"]


def test_superadmin_mark_all_notifications_read_without_organization(app, client):
    from app.users.services import create_user
    from tests.test_targets import PASSWORD

    with app.app_context():
        create_user("super-qa-notif@test.com", PASSWORD, role="superadmin", organization_id=None)
        db.session.commit()
    _login(client, "super-qa-notif@test.com")
    response = client.post("/api/notifications/read-all")
    assert response.status_code == 200
    assert response.get_json()["data"]["marked_read"] == 0


def test_compose_page_has_one_body_field(app, client):
    with app.app_context():
        org, admin = _org("qa-compose", role="admin")
        lead = _lead(org, admin)
        lead_id = lead.id
    _login(client, "admin-qa-compose@test.com")
    html = client.get(f"/leads/{lead_id}/email/compose").get_data(as_text=True)
    # hidden_tag() used to print a second, empty body_html before the editor's
    assert html.count('name="body_html"') == 1
    assert html.count('name="lead_ids"') == 0


def test_leads_list_bulk_form_has_one_hidden_id_field_and_bulk_change_works(app, client):
    from app.leads.models import PipelineStage

    with app.app_context():
        org, admin = _org("qa-bulk", role="admin")
        lead = _lead(org, admin)
        lead_id = lead.id
        target = PipelineStage.query.filter_by(organization_id=org.id).order_by(PipelineStage.order_index).all()[1]
        target_id = target.id
    _login(client, "admin-qa-bulk@test.com")
    html = client.get("/leads/").get_data(as_text=True)
    assert html.count('type="hidden" name="lead_ids"') + html.count('name="lead_ids" type="hidden"') == 1
    # what the browser sends: an empty value first would have hidden the JSON list
    response = client.post(
        "/leads/bulk",
        data={"action": "change_stage", "stage_id": str(target_id), "lead_ids": ["", f"[{lead_id}]"]},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Lead, lead_id).stage_id == target_id


def test_email_with_a_system_template_sends_even_when_templates_were_not_seeded(app, client):
    """The real browser path: GET compose (renders template ids), POST send
    with the chosen id. System templates used to be seeded without a commit,
    so the ids changed between the two requests and the form never validated."""
    import re
    from unittest.mock import patch

    from app.email.models import EmailTemplate

    with app.app_context():
        EmailTemplate.query.filter(EmailTemplate.organization_id.is_(None)).delete()
        org, admin = _org("qa-email-send", role="admin")
        lead = _lead(org, admin)
        lead_id = lead.id
        db.session.commit()
    _login(client, "admin-qa-email-send@test.com")
    html = client.get(f"/leads/{lead_id}/email/compose").get_data(as_text=True)
    template_ids = [int(v) for v in re.findall(r'<option value="(\d+)">', html) if v != "0"]
    assert template_ids
    with app.app_context():
        # a real request ends here: what was only flushed is gone
        db.session.rollback()
        assert db.session.get(EmailTemplate, template_ids[0]) is not None
    csrf = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html)
    data = {"subject": "Putkiremontti", "body_html": "<p>Hei</p>", "template_id": str(template_ids[0])}
    if csrf:
        data["csrf_token"] = csrf.group(1)
    with patch("app.email.services._mailgun_send") as send:
        send.return_value = (True, "<msg@test>", None)
        response = client.post(f"/leads/{lead_id}/email/send", data=data)
    assert response.status_code == 302
    assert "/email/history" in response.headers["Location"]
    assert send.called
