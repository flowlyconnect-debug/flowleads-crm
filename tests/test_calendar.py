from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.calendar.encryption import decrypt_token, encrypt_token
from app.calendar.models import CalendarConnection, CalendarEvent
from app.calendar.services import CalendarService
from app.extensions import db
from app.leads.models import ACTIVITY_TYPES, Activity
from app.leads.services import LeadService, get_default_stage
from app.users.services import create_organization, create_user


def _setup_org(app, slug="cal-org"):
    with app.app_context():
        org = create_organization("Calendar Org", slug)
        db.session.flush()
        user = create_user(
            f"user-{slug}@acme.com",
            "securepassword1",
            role="user",
            organization_id=org.id,
        )
        db.session.commit()
        stage = get_default_stage(org.id)
        return {"org_id": org.id, "user_id": user.id, "user_email": user.email, "stage_id": stage.id}


def _login(client, email):
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "securepassword1"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _create_connection(app, user_id, org_id, *, expires_in_minutes=60):
    with app.app_context():
        conn = CalendarConnection(
            user_id=user_id,
            organization_id=org_id,
            provider="google",
            access_token=encrypt_token("plain-access"),
            refresh_token=encrypt_token("plain-refresh"),
            token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes),
            calendar_id="primary",
            sync_enabled=True,
        )
        db.session.add(conn)
        db.session.commit()
        return conn.id


def test_encrypt_decrypt_differs_from_plaintext(app):
    with app.app_context():
        plain = "my-secret-oauth-token"
        encrypted = encrypt_token(plain)
        assert encrypted != plain
        assert decrypt_token(encrypted) == plain


def test_connection_stores_encrypted_tokens_only(app):
    ctx = _setup_org(app)
    conn_id = _create_connection(app, ctx["user_id"], ctx["org_id"])
    with app.app_context():
        conn = db.session.get(CalendarConnection, conn_id)
        assert conn.access_token != "plain-access"
        assert conn.refresh_token != "plain-refresh"
        assert decrypt_token(conn.access_token) == "plain-access"
        assert decrypt_token(conn.refresh_token) == "plain-refresh"


def test_event_creation_in_db(app):
    ctx = _setup_org(app, "cal-event")
    _create_connection(app, ctx["user_id"], ctx["org_id"])
    with app.app_context():
        from app.calendar.providers import GoogleCalendarProvider

        start = datetime.now(timezone.utc) + timedelta(hours=2)
        end = start + timedelta(minutes=30)
        with patch.object(
            GoogleCalendarProvider,
            "create_event",
            return_value={"external_event_id": "ext-1", "meeting_url": "https://meet.google.com/abc"},
        ):
            event = CalendarService.create_event(
                ctx["user_id"],
                None,
                "Test meeting",
                start,
                end,
                organization_id=ctx["org_id"],
            )
            db.session.commit()
        assert event.id is not None
        assert event.external_event_id == "ext-1"
        assert event.meeting_url == "https://meet.google.com/abc"


def test_refresh_when_expiry_under_5_minutes(app):
    ctx = _setup_org(app, "cal-refresh")
    conn_id = _create_connection(app, ctx["user_id"], ctx["org_id"], expires_in_minutes=2)
    with app.app_context():
        conn = db.session.get(CalendarConnection, conn_id)
        from app.calendar.providers import GoogleCalendarProvider

        with patch.object(
            GoogleCalendarProvider,
            "refresh_tokens",
            return_value={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            },
        ) as mock_refresh:
            CalendarService.refresh_token_if_needed(conn)
            mock_refresh.assert_called_once()
        assert decrypt_token(conn.access_token) == "new-access"


def test_no_refresh_when_token_still_valid(app):
    ctx = _setup_org(app, "cal-valid")
    conn_id = _create_connection(app, ctx["user_id"], ctx["org_id"], expires_in_minutes=120)
    with app.app_context():
        conn = db.session.get(CalendarConnection, conn_id)
        from app.calendar.providers import GoogleCalendarProvider

        with patch.object(GoogleCalendarProvider, "refresh_tokens") as mock_refresh:
            CalendarService.refresh_token_if_needed(conn)
            mock_refresh.assert_not_called()


def test_meeting_linked_to_lead(app):
    ctx = _setup_org(app, "cal-lead")
    _create_connection(app, ctx["user_id"], ctx["org_id"])
    with app.app_context():
        lead = LeadService.create(
            {"email": "lead@cal.com", "company": "Cal Co", "stage_id": ctx["stage_id"]},
            ctx["org_id"],
            ctx["user_id"],
        )
        db.session.flush()
        lead_id = lead.id
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        end = start + timedelta(minutes=30)
        from app.calendar.providers import GoogleCalendarProvider

        with patch.object(
            GoogleCalendarProvider,
            "create_event",
            return_value={"external_event_id": "ext-lead", "meeting_url": None},
        ):
            event = CalendarService.create_event(
                ctx["user_id"],
                lead_id,
                "Lead meeting",
                start,
                end,
                attendees=["lead@cal.com"],
                organization_id=ctx["org_id"],
            )
            db.session.commit()
        assert event.lead_id == lead_id


def test_activity_meeting_scheduled(app):
    assert "meeting_scheduled" in ACTIVITY_TYPES
    ctx = _setup_org(app, "cal-act")
    _create_connection(app, ctx["user_id"], ctx["org_id"])
    with app.app_context():
        lead = LeadService.create(
            {"email": "act@cal.com", "company": "Act Co", "stage_id": ctx["stage_id"]},
            ctx["org_id"],
            ctx["user_id"],
        )
        db.session.flush()
        lead_id = lead.id
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        end = start + timedelta(minutes=30)
        from app.calendar.providers import GoogleCalendarProvider

        with patch.object(
            GoogleCalendarProvider,
            "create_event",
            return_value={"external_event_id": "ext-act", "meeting_url": None},
        ):
            CalendarService.create_event(
                ctx["user_id"],
                lead_id,
                "Scheduled",
                start,
                end,
                organization_id=ctx["org_id"],
            )
            db.session.commit()
        activity = Activity.query.filter_by(lead_id=lead_id, type="meeting_scheduled").first()
        assert activity is not None


def test_cancellation_and_activity(app):
    ctx = _setup_org(app, "cal-cancel")
    _create_connection(app, ctx["user_id"], ctx["org_id"])
    with app.app_context():
        lead = LeadService.create(
            {"email": "cancel@cal.com", "company": "Cancel Co", "stage_id": ctx["stage_id"]},
            ctx["org_id"],
            ctx["user_id"],
        )
        db.session.flush()
        lead_id = lead.id
        event = CalendarEvent(
            user_id=ctx["user_id"],
            lead_id=lead_id,
            organization_id=ctx["org_id"],
            provider="google",
            title="To cancel",
            start_at=datetime.now(timezone.utc) + timedelta(days=1),
            end_at=datetime.now(timezone.utc) + timedelta(days=1, hours=1),
            external_event_id="ext-cancel",
            status="scheduled",
        )
        db.session.add(event)
        db.session.commit()
        event_id = event.id

        from app.calendar.providers import GoogleCalendarProvider

        with patch.object(GoogleCalendarProvider, "delete_event"):
            CalendarService.cancel_event(event_id, ctx["user_id"], ctx["org_id"])
            db.session.commit()

        updated = db.session.get(CalendarEvent, event_id)
        assert updated.status == "cancelled"
        activity = Activity.query.filter_by(lead_id=lead_id, type="meeting_cancelled").first()
        assert activity is not None


def test_disconnect_removes_connection(app):
    ctx = _setup_org(app, "cal-disc")
    _create_connection(app, ctx["user_id"], ctx["org_id"])
    with app.app_context():
        CalendarService.disconnect(ctx["user_id"], ctx["org_id"])
        db.session.commit()
        assert (
            CalendarConnection.query.filter_by(
                user_id=ctx["user_id"], organization_id=ctx["org_id"]
            ).count()
            == 0
        )


def test_calendar_test_route_returns_calendars(app, client):
    ctx = _setup_org(app, "cal-test-route")
    _create_connection(app, ctx["user_id"], ctx["org_id"])
    _login(client, ctx["user_email"])
    with patch(
        "app.calendar.services.CalendarService.test_connection",
        return_value=[{"id": "primary", "summary": "Primary"}],
    ):
        response = client.get("/settings/calendar/test")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["calendars"][0]["summary"] == "Primary"


def _create_event_direct(app, *, user_id, org_id, title, start_at, end_at, lead_id=None):
    with app.app_context():
        event = CalendarEvent(
            user_id=user_id,
            organization_id=org_id,
            lead_id=lead_id,
            provider="google",
            title=title,
            start_at=start_at,
            end_at=end_at,
            status="scheduled",
        )
        db.session.add(event)
        db.session.commit()
        return event.id


def test_calendar_tabs(app, client):
    ctx = _setup_org(app, "cal-tabs")
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=10, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(hours=1)
    tomorrow = today_start + timedelta(days=1)
    other_org = _setup_org(app, "cal-tabs-other")

    _create_event_direct(
        app,
        user_id=ctx["user_id"],
        org_id=ctx["org_id"],
        title="Today meeting",
        start_at=today_start,
        end_at=today_end,
    )
    _create_event_direct(
        app,
        user_id=ctx["user_id"],
        org_id=ctx["org_id"],
        title="Tomorrow meeting",
        start_at=tomorrow,
        end_at=tomorrow + timedelta(hours=1),
    )
    _create_event_direct(
        app,
        user_id=other_org["user_id"],
        org_id=other_org["org_id"],
        title="Other org secret",
        start_at=today_start,
        end_at=today_end,
    )

    for i in range(6):
        future = now + timedelta(days=i + 1, hours=i + 2)
        _create_event_direct(
            app,
            user_id=ctx["user_id"],
            org_id=ctx["org_id"],
            title=f"Upcoming {i}",
            start_at=future,
            end_at=future + timedelta(hours=1),
        )

    _login(client, ctx["user_email"])

    day_resp = client.get("/calendar?view=day")
    assert day_resp.status_code == 200
    day_body = day_resp.get_data(as_text=True)
    assert "Today meeting" in day_body
    assert 'calendar-day-item__title">Tomorrow meeting' not in day_body
    assert "Other org secret" not in day_body

    week_resp = client.get("/calendar?view=week")
    assert week_resp.status_code == 200
    week_body = week_resp.get_data(as_text=True)
    assert "calendar-week-grid" in week_body
    assert "Ma " in week_body or "Ti " in week_body
    assert "Today meeting" in week_body
    assert "Tomorrow meeting" in week_body
    assert "Other org secret" not in week_body

    default_resp = client.get("/calendar")
    assert default_resp.status_code == 200
    assert "Viikko" in default_resp.get_data(as_text=True)

    sidebar_resp = client.get("/calendar?view=week")
    sidebar_body = sidebar_resp.get_data(as_text=True)
    assert "Tulevat tapaamiset" in sidebar_body
    upcoming_items = sidebar_body.count('class="calendar-upcoming-item"')
    assert upcoming_items == 5
    assert "Other org secret" not in sidebar_body
    assert "Tomorrow meeting" in sidebar_body

    with app.app_context():
        data = CalendarService.get_calendar_page_data(ctx["user_id"], ctx["org_id"])
        assert all(e.organization_id == ctx["org_id"] for e in data["today_events"])
        assert all(e.organization_id == ctx["org_id"] for e in data["upcoming_events"])
        assert len(data["upcoming_events"]) == 5


def test_disconnect_route(app, client):
    ctx = _setup_org(app, "cal-disc-route")
    _create_connection(app, ctx["user_id"], ctx["org_id"])
    _login(client, ctx["user_email"])
    response = client.post("/settings/calendar/disconnect", follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        assert CalendarConnection.query.filter_by(user_id=ctx["user_id"]).count() == 0
