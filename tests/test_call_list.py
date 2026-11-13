"""Soittolista: transfers put new leads on today's call list, and a call's
result is recorded in one click."""

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.leads.models import Activity, Lead
from app.listings import target_services as targets
from app.tasks import call_list as calls
from app.tasks.models import Task
from tests.test_targets import _listing, _login, _match_ids, _org, _profile


def _transfer(org, user):
    result = targets.transfer_targets(org.id, _match_ids(org), assigned_to=user.id)
    db.session.commit()
    return result["lead_ids"]


def _open_tasks(lead_id):
    return Task.query.filter(Task.lead_id == lead_id, Task.status.in_(("pending", "in_progress"))).all()


def test_auto_task_becomes_the_call_and_leads_without_phone_are_skipped(app):
    with app.app_context():
        org, user = _org("calls-prepare", role="admin")
        _profile(org)
        _listing(1, taloyhtio="As Oy Puhelin", isannoitsija_puh="040 000 0101")
        _listing(2, taloyhtio="As Oy Ei Numeroa")
        lead_ids = _transfer(org, user)
        phone_lead = Lead.query.filter_by(organization_id=org.id, company="As Oy Puhelin").one()
        assert [t.type for t in _open_tasks(phone_lead.id)] == ["follow_up"]  # the automatic "Ota yhteyttä"

        result = calls.prepare_call_tasks(org.id, lead_ids, user_id=user.id)
        db.session.commit()
        assert result == {"added": 1, "no_phone": 1, "already": 0}
        tasks = _open_tasks(phone_lead.id)
        assert len(tasks) == 1 and tasks[0].type == "call" and tasks[0].title == "Soita: As Oy Puhelin"

        again = calls.prepare_call_tasks(org.id, lead_ids, user_id=user.id)
        assert again["already"] == 1 and again["added"] == 0
        assert len(_open_tasks(phone_lead.id)) == 1


def test_call_list_shows_due_calls_and_counts_later_ones(app):
    with app.app_context():
        org, user = _org("calls-list", role="admin")
        _profile(org)
        _listing(1, taloyhtio="As Oy Tänään", isannoitsija_puh="040 1")
        _listing(2, taloyhtio="As Oy Myöhemmin", isannoitsija_puh="040 2")
        lead_ids = _transfer(org, user)
        calls.prepare_call_tasks(org.id, lead_ids, user_id=user.id)
        later = Task.query.filter_by(organization_id=org.id, title="Soita: As Oy Myöhemmin").one()
        later.due_date = datetime.now(timezone.utc) + timedelta(days=3)
        db.session.commit()

        data = calls.call_list(org.id, user_id=user.id)
        assert [t.title for t in data["due"]] == ["Soita: As Oy Tänään"]
        assert data["upcoming"] == 1
        assert calls.count_due_calls(org.id, user.id) == 1
        row = calls.call_row(data["due"][0])
        assert row["phone"] == "040 1" and row["renovation"] == "Lukkoremontti / kulunvalvonta"
        assert row["late"] is False


def test_outcomes_move_the_call_on_and_log_it(app):
    with app.app_context():
        org, user = _org("calls-outcome", role="admin")
        _profile(org)
        _listing(1, taloyhtio="As Oy Soitto", isannoitsija_puh="040 1")
        lead_ids = _transfer(org, user)
        calls.prepare_call_tasks(org.id, lead_ids, user_id=user.id)
        db.session.commit()
        task = Task.query.filter_by(organization_id=org.id, type="call").one()
        lead_id = task.lead_id

        calls.record_call_outcome(task.id, org.id, user_id=user.id, outcome="no_answer")
        db.session.commit()
        assert task.status == "pending"
        assert calls.count_due_calls(org.id, user.id) == 0  # moved to tomorrow morning
        assert db.session.get(Lead, lead_id).last_contacted_at is None

        calls.record_call_outcome(task.id, org.id, user_id=user.id, outcome="offer", note="Kokous 3.12.")
        db.session.commit()
        assert task.status == "completed"
        follow_up = Task.query.filter_by(lead_id=lead_id, title="Tee tarjous").one()
        assert follow_up.status == "pending" and follow_up.priority == "high"
        assert db.session.get(Lead, lead_id).last_contacted_at is not None
        logged = [a.content for a in Activity.query.filter_by(lead_id=lead_id, type="call").order_by(Activity.id)]
        assert logged == ["Ei vastannut", "Pyysi tarjouksen: Kokous 3.12."]


def test_call_list_page_and_transfer_option(app, client):
    with app.app_context():
        org, user = _org("calls-page", role="admin")
        _profile(org)
        _listing(1, taloyhtio="As Oy Soittolista", isannoitsija_puh="040 000 0101", isannoitsija_henkilo="Laura Mäkinen")
        org_id, email = org.id, user.email
        ids = _match_ids(org)

    _login(client, email)
    assert "Soittolista on tyhjä" in client.get("/tasks/calls").get_data(as_text=True)
    assert 'name="call_list"' in client.get("/targets").get_data(as_text=True)

    response = client.post("/targets/transfer", data={"match_ids": ids, "call_list": "1"}, follow_redirects=True)
    assert "1 liidi lisätty soittolistalle" in response.get_data(as_text=True)

    page = client.get("/tasks/calls").get_data(as_text=True)
    assert "As Oy Soittolista" in page and 'href="tel:0400000101"' in page and "Laura Mäkinen" in page
    with app.app_context():
        task_id = Task.query.filter_by(organization_id=org_id, type="call").one().id

    done = client.post(f"/tasks/{task_id}/call-outcome", data={"outcome": "meeting"}, follow_redirects=True)
    html = done.get_data(as_text=True)
    assert "Tapaaminen sovittu. Puhelu kirjattiin liidille." in html
    assert "Soittolista on tyhjä" in html
    bad = client.post(f"/tasks/{task_id}/call-outcome", data={"outcome": "meeting"}, follow_redirects=True)
    assert "Tämä puhelu on jo kirjattu." in bad.get_data(as_text=True)


def test_user_cannot_record_another_users_call(app, client):
    from app.users.services import create_user

    with app.app_context():
        org, admin = _org("calls-own", role="admin")
        seller = create_user("seller-calls@test.com", "securepassword1", role="user", organization_id=org.id)
        seller.totp_enabled = True
        _profile(org)
        _listing(1, isannoitsija_puh="040 1")
        lead_ids = _transfer(org, admin)
        calls.prepare_call_tasks(org.id, lead_ids, user_id=admin.id)
        db.session.commit()
        task_id = Task.query.filter_by(organization_id=org.id, type="call").one().id

    _login(client, "seller-calls@test.com")
    response = client.post(f"/tasks/{task_id}/call-outcome", data={"outcome": "meeting"}, follow_redirects=True)
    assert "Voit kirjata vain omia puheluitasi." in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Task, task_id).status == "pending"
