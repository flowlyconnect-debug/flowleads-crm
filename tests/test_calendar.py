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


def test_disconnect_route(app, client):
    ctx = _setup_org(app, "cal-disc-route")
    _create_connection(app, ctx["user_id"], ctx["org_id"])
    _login(client, ctx["user_email"])
    response = client.post("/settings/calendar/disconnect", follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        assert CalendarConnection.query.filter_by(user_id=ctx["user_id"]).count() == 0
