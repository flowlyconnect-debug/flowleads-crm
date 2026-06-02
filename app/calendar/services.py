from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import joinedload

from app.calendar.encryption import decrypt_token, encrypt_token
from app.calendar.models import CALENDAR_PROVIDERS, EVENT_STATUSES, CalendarConnection, CalendarEvent
from app.calendar.providers import GoogleCalendarProvider, MicrosoftCalendarProvider
from app.extensions import db
from app.leads.models import ACTIVITY_TYPES, Lead
from app.leads.services import LeadService, LeadServiceError, get_lead_for_org

logger = logging.getLogger(__name__)

TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


class CalendarServiceError(Exception):
    def __init__(self, message: str, code: str = "calendar_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _provider_client(provider: str):
    if provider == "google":
        return GoogleCalendarProvider
    if provider == "microsoft":
        return MicrosoftCalendarProvider
    raise CalendarServiceError("Unsupported calendar provider.", "invalid_provider")


def _log_meeting_activity(
    lead_id: int,
    user_id: int | None,
    activity_type: str,
    *,
    content: str | None = None,
    metadata: dict | None = None,
) -> None:
    if activity_type not in ACTIVITY_TYPES:
        raise CalendarServiceError("Invalid activity type.", "invalid_activity")
    meta = dict(metadata or {})
    LeadService.log_activity(
        lead_id,
        user_id,
        activity_type,
        content=content,
        metadata=meta,
    )


class CalendarService:
    @staticmethod
    def get_active_connection(user_id: int, organization_id: int) -> CalendarConnection | None:
        return (
            CalendarConnection.query.filter_by(
                user_id=user_id,
                organization_id=organization_id,
            )
            .order_by(CalendarConnection.created_at.desc())
            .first()
        )

    @staticmethod
    def set_connection_tokens(
        connection: CalendarConnection,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
    ) -> None:
        connection.access_token = encrypt_token(access_token)
        if refresh_token:
            connection.refresh_token = encrypt_token(refresh_token)
        connection.token_expires_at = expires_at

    @staticmethod
    def get_access_token(connection: CalendarConnection) -> str:
        return decrypt_token(connection.access_token)

    @staticmethod
    def get_refresh_token(connection: CalendarConnection) -> str | None:
        if not connection.refresh_token:
            return None
        return decrypt_token(connection.refresh_token)

    @staticmethod
    def refresh_token_if_needed(connection: CalendarConnection) -> CalendarConnection:
        if not connection.token_expires_at:
            return connection
        expires = _ensure_tz(connection.token_expires_at)
        if expires > _utc_now() + TOKEN_REFRESH_BUFFER:
            return connection

        refresh_token = CalendarService.get_refresh_token(connection)
        if not refresh_token:
            raise CalendarServiceError(
                "Calendar token expired and no refresh token is available.",
                "token_expired",
            )

        client = _provider_client(connection.provider)
        result = client.refresh_tokens(refresh_token)
        CalendarService.set_connection_tokens(
            connection,
            result["access_token"],
            result.get("refresh_token"),
            result.get("expires_at"),
        )
        db.session.flush()
        return connection

    @staticmethod
    def save_oauth_connection(
        user_id: int,
        organization_id: int,
        provider: str,
        *,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        calendar_id: str | None = None,
    ) -> CalendarConnection:
        if provider not in CALENDAR_PROVIDERS:
            raise CalendarServiceError("Invalid provider.", "invalid_provider")

        existing = CalendarConnection.query.filter_by(
            user_id=user_id,
            provider=provider,
        ).first()
        if existing:
            connection = existing
        else:
            connection = CalendarConnection(
                user_id=user_id,
                organization_id=organization_id,
                provider=provider,
                access_token="",
            )
            db.session.add(connection)

        connection.organization_id = organization_id
        CalendarService.set_connection_tokens(
            connection, access_token, refresh_token, expires_at
        )

        if not calendar_id:
            client = _provider_client(provider)
            access = access_token
            if provider == "google":
                calendar_id = client.get_primary_calendar_id(access)
            else:
                calendar_id = client.get_default_calendar_id(access)
        connection.calendar_id = calendar_id
        connection.sync_enabled = True
        db.session.flush()
        return connection

    @staticmethod
    def disconnect(user_id: int, organization_id: int) -> None:
        connections = CalendarConnection.query.filter_by(
            user_id=user_id,
            organization_id=organization_id,
        ).all()
        for conn in connections:
            db.session.delete(conn)

    @staticmethod
    def test_connection(connection: CalendarConnection) -> list[dict[str, str]]:
        CalendarService.refresh_token_if_needed(connection)
        access = CalendarService.get_access_token(connection)
        client = _provider_client(connection.provider)
        return client.list_calendars(access)

    @staticmethod
    def create_event(
        user_id: int,
        lead_id: int | None,
        title: str,
        start_at: datetime,
        end_at: datetime,
        description: str | None = None,
        attendees: list[str] | None = None,
        *,
        video_meeting: bool = True,
        location: str | None = None,
        organization_id: int | None = None,
    ) -> CalendarEvent:
        if organization_id is None:
            from app.users.models import User

            user = db.session.get(User, user_id)
            if not user or not user.organization_id:
                raise CalendarServiceError("User organization not found.", "not_found")
            organization_id = user.organization_id

        connection = CalendarService.get_active_connection(user_id, organization_id)
        if not connection:
            raise CalendarServiceError(
                "No calendar connection. Connect Google or Microsoft in settings.",
                "no_connection",
            )

        if lead_id is not None:
            get_lead_for_org(lead_id, organization_id)

        attendee_list = [e.strip() for e in (attendees or []) if e and e.strip()]
        start_at = _ensure_tz(start_at)
        end_at = _ensure_tz(end_at)

        event = CalendarEvent(
            user_id=user_id,
            lead_id=lead_id,
            organization_id=organization_id,
            provider=connection.provider,
            title=title.strip(),
            description=description,
            start_at=start_at,
            end_at=end_at,
            location=location,
            attendees=attendee_list,
            is_synced=False,
            status="scheduled",
        )
        db.session.add(event)
        db.session.flush()

        CalendarService.refresh_token_if_needed(connection)
        access = CalendarService.get_access_token(connection)
        if not connection.calendar_id:
            raise CalendarServiceError("Calendar ID not configured.", "no_calendar")

        client = _provider_client(connection.provider)
        result = client.create_event(
            access,
            connection.calendar_id,
            title=event.title,
            description=event.description,
            start_at=event.start_at,
            end_at=event.end_at,
            attendees=attendee_list,
            location=location,
            video_meeting=video_meeting,
        )
        event.external_event_id = result["external_event_id"]
        event.meeting_url = result.get("meeting_url")
        event.is_synced = True
        event.updated_at = _utc_now()

        if lead_id is not None:
            _log_meeting_activity(
                lead_id,
                user_id,
                "meeting_scheduled",
                content=title,
                metadata={
                    "event_id": event.id,
                    "start_at": start_at.isoformat(),
                    "meeting_url": event.meeting_url,
                },
            )

        db.session.flush()
        return event

    @staticmethod
    def cancel_event(event_id: int, user_id: int, organization_id: int) -> CalendarEvent:
        event = (
            CalendarEvent.query.filter_by(
                id=event_id,
                user_id=user_id,
                organization_id=organization_id,
            )
            .first()
        )
        if not event:
            raise CalendarServiceError("Event not found.", "not_found")
        if event.status == "cancelled":
            return event

        connection = CalendarService.get_active_connection(user_id, organization_id)
        if connection and event.external_event_id and connection.calendar_id:
            try:
                CalendarService.refresh_token_if_needed(connection)
                access = CalendarService.get_access_token(connection)
                client = _provider_client(connection.provider)
                client.delete_event(access, connection.calendar_id, event.external_event_id)
            except Exception:
                logger.exception(
                    "Failed to delete event %s from provider %s",
                    event.external_event_id,
                    connection.provider,
                )

        event.status = "cancelled"
        event.updated_at = _utc_now()

        if event.lead_id:
            _log_meeting_activity(
                event.lead_id,
                user_id,
                "meeting_cancelled",
                content=event.title,
                metadata={"event_id": event.id},
            )

        db.session.flush()
        return event

    @staticmethod
    def get_events_for_lead(lead_id: int, organization_id: int) -> dict[str, list[CalendarEvent]]:
        now = _utc_now()
        events = (
            CalendarEvent.query.filter_by(lead_id=lead_id, organization_id=organization_id)
            .filter(CalendarEvent.status != "cancelled")
            .order_by(CalendarEvent.start_at.asc())
            .all()
        )
        upcoming = [e for e in events if _ensure_tz(e.end_at) >= now]
        past = [e for e in events if _ensure_tz(e.end_at) < now]
        past.reverse()
        return {"upcoming": upcoming, "past": past}

    @staticmethod
    def get_week_events(user_id: int, organization_id: int) -> dict[str, list[CalendarEvent]]:
        """Legacy split; prefer get_calendar_page_data for the unified calendar UI."""
        data = CalendarService.get_calendar_page_data(user_id, organization_id)
        return {"today": data["today_events"], "week": []}

    @staticmethod
    def get_calendar_page_data(user_id: int, organization_id: int) -> dict:
        """Today list, Mon–Sun week grid, and next upcoming events for the calendar page."""
        now = _utc_now()
        today = now.date()
        week_start_date = today - timedelta(days=today.weekday())
        week_start = datetime.combine(week_start_date, datetime.min.time(), tzinfo=timezone.utc)
        week_end = week_start + timedelta(days=7)
        weekday_labels = ("Ma", "Ti", "Ke", "To", "Pe", "La", "Su")

        events = (
            CalendarEvent.query.filter_by(user_id=user_id, organization_id=organization_id)
            .options(joinedload(CalendarEvent.lead))
            .filter(
                CalendarEvent.status != "cancelled",
                CalendarEvent.start_at < week_end,
                CalendarEvent.end_at >= week_start,
            )
            .order_by(CalendarEvent.start_at.asc())
            .all()
        )

        today_events = [e for e in events if _ensure_tz(e.start_at).date() == today]

        week_days = []
        for offset in range(7):
            day_date = week_start_date + timedelta(days=offset)
            day_events = [e for e in events if _ensure_tz(e.start_at).date() == day_date]
            week_days.append(
                {
                    "date": day_date,
                    "label": f"{weekday_labels[offset]} {day_date.day}.{day_date.month}.",
                    "is_today": day_date == today,
                    "events": day_events,
                }
            )

        upcoming_events = (
            CalendarEvent.query.filter_by(user_id=user_id, organization_id=organization_id)
            .options(joinedload(CalendarEvent.lead))
            .filter(
                CalendarEvent.status != "cancelled",
                CalendarEvent.start_at >= now,
            )
            .order_by(CalendarEvent.start_at.asc())
            .limit(5)
            .all()
        )

        return {
            "today_events": today_events,
            "week_days": week_days,
            "week_has_events": bool(events),
            "upcoming_events": upcoming_events,
        }

    @staticmethod
    def get_upcoming_meetings(
        user_id: int, organization_id: int, limit: int = 3
    ) -> list[CalendarEvent]:
        now = _utc_now()
        return (
            CalendarEvent.query.filter_by(user_id=user_id, organization_id=organization_id)
            .options(joinedload(CalendarEvent.lead))
            .filter(
                CalendarEvent.status != "cancelled",
                CalendarEvent.start_at >= now,
            )
            .order_by(CalendarEvent.start_at.asc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def sync_upcoming(user_id: int) -> int:
        from app.users.models import User

        user = db.session.get(User, user_id)
        if not user or not user.organization_id:
            return 0

        connection = CalendarService.get_active_connection(user_id, user.organization_id)
        if not connection or not connection.sync_enabled:
            return 0

        try:
            CalendarService.refresh_token_if_needed(connection)
            access = CalendarService.get_access_token(connection)
            if not connection.calendar_id:
                return 0

            now = _utc_now()
            time_max = now + timedelta(days=7)
            client = _provider_client(connection.provider)
            remote_events = client.list_events(
                access,
                connection.calendar_id,
                time_min=now,
                time_max=time_max,
            )

            lead_emails = {
                row.email.lower(): row.id
                for row in Lead.query.filter_by(
                    organization_id=user.organization_id,
                    status="active",
                ).filter(Lead.email.isnot(None))
                .all()
                if row.email
            }

            synced = 0
            for remote in remote_events:
                lead_id = None
                for email in remote.get("attendees") or []:
                    if email and email.lower() in lead_emails:
                        lead_id = lead_emails[email.lower()]
                        break

                existing = None
                if remote.get("external_event_id"):
                    existing = CalendarEvent.query.filter_by(
                        user_id=user_id,
                        organization_id=user.organization_id,
                        external_event_id=remote["external_event_id"],
                    ).first()

                if existing:
                    existing.title = remote["title"]
                    existing.description = remote.get("description")
                    existing.start_at = _ensure_tz(remote["start_at"])
                    existing.end_at = _ensure_tz(remote["end_at"])
                    existing.location = remote.get("location")
                    existing.meeting_url = remote.get("meeting_url")
                    existing.attendees = remote.get("attendees") or []
                    existing.is_synced = True
                    existing.status = remote.get("status", "scheduled")
                    if lead_id and not existing.lead_id:
                        existing.lead_id = lead_id
                    existing.updated_at = _utc_now()
                else:
                    event = CalendarEvent(
                        user_id=user_id,
                        lead_id=lead_id,
                        organization_id=user.organization_id,
                        external_event_id=remote["external_event_id"],
                        provider=connection.provider,
                        title=remote["title"],
                        description=remote.get("description"),
                        start_at=_ensure_tz(remote["start_at"]),
                        end_at=_ensure_tz(remote["end_at"]),
                        location=remote.get("location"),
                        meeting_url=remote.get("meeting_url"),
                        attendees=remote.get("attendees") or [],
                        is_synced=True,
                        status=remote.get("status", "scheduled"),
                    )
                    db.session.add(event)
                synced += 1

            connection.last_synced_at = _utc_now()
            db.session.flush()
            return synced
        except Exception:
            logger.exception(
                "Calendar sync failed for user_id=%s provider=%s",
                user_id,
                connection.provider,
            )
            raise

    @staticmethod
    def run_hourly_sync() -> int:
        """Sync all users with sync_enabled connections. Isolates per-user failures."""
        connections = (
            CalendarConnection.query.filter_by(sync_enabled=True)
            .order_by(CalendarConnection.user_id)
            .all()
        )
        total = 0
        for connection in connections:
            try:
                count = CalendarService.sync_upcoming(connection.user_id)
                db.session.commit()
                total += count
            except Exception:
                db.session.rollback()
                logger.exception(
                    "Hourly calendar sync failed for user_id=%s org_id=%s provider=%s",
                    connection.user_id,
                    connection.organization_id,
                    connection.provider,
                )
        return total
