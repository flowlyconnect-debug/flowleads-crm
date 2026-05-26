"""Google Calendar and Microsoft Graph provider clients (mockable in tests)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from flask import current_app

logger = logging.getLogger(__name__)

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
MICROSOFT_SCOPES = ["Calendars.ReadWrite", "OnlineMeetings.ReadWrite"]
MICROSOFT_AUTHORITY = "https://login.microsoftonline.com/common"


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _google_oauth_config() -> dict[str, str]:
    return {
        "client_id": current_app.config["GOOGLE_CLIENT_ID"],
        "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": current_app.config["GOOGLE_REDIRECT_URI"],
    }


def _microsoft_oauth_config() -> dict[str, str]:
    return {
        "client_id": current_app.config["MICROSOFT_CLIENT_ID"],
        "client_secret": current_app.config["MICROSOFT_CLIENT_SECRET"],
        "redirect_uri": current_app.config["MICROSOFT_REDIRECT_URI"],
    }


class GoogleCalendarProvider:
    @staticmethod
    def get_authorization_url(state: str) -> str:
        from google_auth_oauthlib.flow import Flow

        cfg = _google_oauth_config()
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [cfg["redirect_uri"]],
                }
            },
            scopes=GOOGLE_SCOPES,
            state=state,
        )
        flow.redirect_uri = cfg["redirect_uri"]
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return auth_url

    @staticmethod
    def exchange_code(code: str) -> dict[str, Any]:
        from google_auth_oauthlib.flow import Flow

        cfg = _google_oauth_config()
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [cfg["redirect_uri"]],
                }
            },
            scopes=GOOGLE_SCOPES,
        )
        flow.redirect_uri = cfg["redirect_uri"]
        flow.fetch_token(code=code)
        creds = flow.credentials
        expires_at = None
        if creds.expiry:
            expires_at = _ensure_tz(creds.expiry)
        return {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "expires_at": expires_at,
        }

    @staticmethod
    def refresh_tokens(refresh_token: str) -> dict[str, Any]:
        import google.oauth2.credentials
        import google.auth.transport.requests
        from google_auth_oauthlib.flow import Flow

        cfg = _google_oauth_config()
        creds = google.oauth2.credentials.Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cfg["client_id"],
            client_secret=cfg["client_secret"],
        )
        creds.refresh(google.auth.transport.requests.Request())
        expires_at = None
        if creds.expiry:
            expires_at = _ensure_tz(creds.expiry)
        return {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token or refresh_token,
            "expires_at": expires_at,
        }

    @staticmethod
    def _build_service(access_token: str):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(token=access_token)
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    @staticmethod
    def list_calendars(access_token: str) -> list[dict[str, str]]:
        service = GoogleCalendarProvider._build_service(access_token)
        result = service.calendarList().list().execute()
        items = result.get("items", [])
        return [
            {"id": item["id"], "summary": item.get("summary", item["id"])}
            for item in items
        ]

    @staticmethod
    def get_primary_calendar_id(access_token: str) -> str:
        service = GoogleCalendarProvider._build_service(access_token)
        primary = service.calendars().get(calendarId="primary").execute()
        return primary["id"]

    @staticmethod
    def create_event(
        access_token: str,
        calendar_id: str,
        *,
        title: str,
        description: str | None,
        start_at: datetime,
        end_at: datetime,
        attendees: list[str],
        location: str | None,
        video_meeting: bool,
    ) -> dict[str, Any]:
        service = GoogleCalendarProvider._build_service(access_token)
        body: dict[str, Any] = {
            "summary": title,
            "description": description or "",
            "start": {"dateTime": _ensure_tz(start_at).isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": _ensure_tz(end_at).isoformat(), "timeZone": "UTC"},
            "attendees": [{"email": e} for e in attendees if e],
        }
        if location:
            body["location"] = location
        if video_meeting:
            import uuid

            body["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
        created = (
            service.events()
            .insert(
                calendarId=calendar_id,
                body=body,
                conferenceDataVersion=1 if video_meeting else 0,
                sendUpdates="all",
            )
            .execute()
        )
        meeting_url = None
        if video_meeting:
            entry_points = (created.get("conferenceData") or {}).get("entryPoints") or []
            for ep in entry_points:
                if ep.get("entryPointType") == "video":
                    meeting_url = ep.get("uri")
                    break
            if not meeting_url:
                meeting_url = created.get("hangoutLink")
        return {
            "external_event_id": created["id"],
            "meeting_url": meeting_url,
        }

    @staticmethod
    def delete_event(access_token: str, calendar_id: str, external_event_id: str) -> None:
        service = GoogleCalendarProvider._build_service(access_token)
        service.events().delete(
            calendarId=calendar_id,
            eventId=external_event_id,
            sendUpdates="all",
        ).execute()

    @staticmethod
    def list_events(
        access_token: str,
        calendar_id: str,
        *,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict[str, Any]]:
        service = GoogleCalendarProvider._build_service(access_token)
        result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=_ensure_tz(time_min).isoformat(),
                timeMax=_ensure_tz(time_max).isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = []
        for item in result.get("items", []):
            if item.get("status") == "cancelled":
                continue
            start = item.get("start", {})
            end = item.get("end", {})
            start_at = start.get("dateTime") or start.get("date")
            end_at = end.get("dateTime") or end.get("date")
            if not start_at or not end_at:
                continue
            attendees = [
                a.get("email")
                for a in item.get("attendees", [])
                if a.get("email")
            ]
            meeting_url = item.get("hangoutLink")
            if not meeting_url:
                for ep in (item.get("conferenceData") or {}).get("entryPoints") or []:
                    if ep.get("entryPointType") == "video":
                        meeting_url = ep.get("uri")
                        break
            events.append(
                {
                    "external_event_id": item["id"],
                    "title": item.get("summary") or "(No title)",
                    "description": item.get("description"),
                    "start_at": datetime.fromisoformat(start_at.replace("Z", "+00:00")),
                    "end_at": datetime.fromisoformat(end_at.replace("Z", "+00:00")),
                    "location": item.get("location"),
                    "meeting_url": meeting_url,
                    "attendees": attendees,
                    "status": "cancelled" if item.get("status") == "cancelled" else "scheduled",
                }
            )
        return events


class MicrosoftCalendarProvider:
    @staticmethod
    def get_authorization_url(state: str) -> str:
        import msal

        cfg = _microsoft_oauth_config()
        app = msal.ConfidentialClientApplication(
            cfg["client_id"],
            authority=MICROSOFT_AUTHORITY,
            client_credential=cfg["client_secret"],
        )
        return app.get_authorization_request_url(
            scopes=MICROSOFT_SCOPES,
            state=state,
            redirect_uri=cfg["redirect_uri"],
        )

    @staticmethod
    def exchange_code(code: str) -> dict[str, Any]:
        import msal

        cfg = _microsoft_oauth_config()
        app = msal.ConfidentialClientApplication(
            cfg["client_id"],
            authority=MICROSOFT_AUTHORITY,
            client_credential=cfg["client_secret"],
        )
        result = app.acquire_token_by_authorization_code(
            code,
            scopes=MICROSOFT_SCOPES,
            redirect_uri=cfg["redirect_uri"],
        )
        if "error" in result:
            raise RuntimeError(result.get("error_description", result.get("error")))
        expires_at = None
        if result.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(result["expires_in"]))
        return {
            "access_token": result["access_token"],
            "refresh_token": result.get("refresh_token"),
            "expires_at": expires_at,
        }

    @staticmethod
    def refresh_tokens(refresh_token: str) -> dict[str, Any]:
        import msal

        cfg = _microsoft_oauth_config()
        app = msal.ConfidentialClientApplication(
            cfg["client_id"],
            authority=MICROSOFT_AUTHORITY,
            client_credential=cfg["client_secret"],
        )
        result = app.acquire_token_by_refresh_token(refresh_token, scopes=MICROSOFT_SCOPES)
        if "error" in result:
            raise RuntimeError(result.get("error_description", result.get("error")))
        expires_at = None
        if result.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(result["expires_in"]))
        return {
            "access_token": result["access_token"],
            "refresh_token": result.get("refresh_token") or refresh_token,
            "expires_at": expires_at,
        }

    @staticmethod
    def _graph_headers(access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    @staticmethod
    def list_calendars(access_token: str) -> list[dict[str, str]]:
        import requests

        resp = requests.get(
            "https://graph.microsoft.com/v1.0/me/calendars",
            headers=MicrosoftCalendarProvider._graph_headers(access_token),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {"id": item["id"], "summary": item.get("name", item["id"])}
            for item in data.get("value", [])
        ]

    @staticmethod
    def get_default_calendar_id(access_token: str) -> str:
        calendars = MicrosoftCalendarProvider.list_calendars(access_token)
        if calendars:
            return calendars[0]["id"]
        return ""

    @staticmethod
    def create_event(
        access_token: str,
        calendar_id: str,
        *,
        title: str,
        description: str | None,
        start_at: datetime,
        end_at: datetime,
        attendees: list[str],
        location: str | None,
        video_meeting: bool,
    ) -> dict[str, Any]:
        import requests

        body: dict[str, Any] = {
            "subject": title,
            "body": {"contentType": "text", "content": description or ""},
            "start": {
                "dateTime": _ensure_tz(start_at).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": _ensure_tz(end_at).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC",
            },
            "attendees": [
                {
                    "emailAddress": {"address": email},
                    "type": "required",
                }
                for email in attendees
                if email
            ],
        }
        if location:
            body["location"] = {"displayName": location}
        if video_meeting:
            body["isOnlineMeeting"] = True
            body["onlineMeetingProvider"] = "teamsForBusiness"

        url = f"https://graph.microsoft.com/v1.0/me/calendars/{calendar_id}/events"
        resp = requests.post(
            url,
            headers=MicrosoftCalendarProvider._graph_headers(access_token),
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        created = resp.json()
        meeting_url = None
        if video_meeting:
            meeting_url = (created.get("onlineMeeting") or {}).get("joinUrl")
        return {
            "external_event_id": created["id"],
            "meeting_url": meeting_url,
        }

    @staticmethod
    def delete_event(access_token: str, calendar_id: str, external_event_id: str) -> None:
        import requests

        url = f"https://graph.microsoft.com/v1.0/me/events/{external_event_id}"
        resp = requests.delete(
            url,
            headers=MicrosoftCalendarProvider._graph_headers(access_token),
            timeout=30,
        )
        if resp.status_code not in (204, 404):
            resp.raise_for_status()

    @staticmethod
    def list_events(
        access_token: str,
        calendar_id: str,
        *,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict[str, Any]]:
        import requests

        params = urlencode(
            {
                "startDateTime": _ensure_tz(time_min).isoformat(),
                "endDateTime": _ensure_tz(time_max).isoformat(),
                "$orderby": "start/dateTime",
            }
        )
        url = f"https://graph.microsoft.com/v1.0/me/calendars/{calendar_id}/calendarView?{params}"
        resp = requests.get(
            url,
            headers=MicrosoftCalendarProvider._graph_headers(access_token),
            timeout=30,
        )
        resp.raise_for_status()
        events = []
        for item in resp.json().get("value", []):
            if item.get("isCancelled"):
                continue
            start_at = datetime.fromisoformat(
                item["start"]["dateTime"].replace("Z", "+00:00")
            )
            end_at = datetime.fromisoformat(item["end"]["dateTime"].replace("Z", "+00:00"))
            attendees = [
                (a.get("emailAddress") or {}).get("address")
                for a in item.get("attendees", [])
            ]
            attendees = [e for e in attendees if e]
            meeting_url = (item.get("onlineMeeting") or {}).get("joinUrl")
            events.append(
                {
                    "external_event_id": item["id"],
                    "title": item.get("subject") or "(No title)",
                    "description": (item.get("body") or {}).get("content"),
                    "start_at": start_at,
                    "end_at": end_at,
                    "location": (item.get("location") or {}).get("displayName"),
                    "meeting_url": meeting_url,
                    "attendees": attendees,
                    "status": "scheduled",
                }
            )
        return events
