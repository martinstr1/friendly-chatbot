from __future__ import annotations
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import google.auth
from googleapiclient.discovery import build
from .config import Settings


def _calendar_service():
    credentials, _ = google.auth.default()
    scoped = credentials.with_scopes(["https://www.googleapis.com/auth/calendar"])
    return build("calendar", "v3", credentials=scoped, cache_discovery=False)


def create_event(summary: str, start: datetime, duration_minutes: int, attendee_email: Optional[str] = None) -> Dict[str, Any]:
    service = _calendar_service()
    end = start + timedelta(minutes=duration_minutes)
    event_body: Dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": Settings.TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": Settings.TIMEZONE},
    }
    if attendee_email:
        event_body["attendees"] = [{"email": attendee_email}]
    event = service.events().insert(calendarId=Settings.CALENDAR_ID, body=event_body).execute()
    return {"eventId": event["id"], "summary": summary, "start": start.isoformat(), "end": end.isoformat(), "attendees": event_body.get("attendees", [])}


def reschedule_event(event_id: str, new_start: datetime, duration_minutes: int) -> Dict[str, Any]:
    service = _calendar_service()
    end = new_start + timedelta(minutes=duration_minutes)
    body = {
        "start": {"dateTime": new_start.isoformat(), "timeZone": Settings.TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": Settings.TIMEZONE},
    }
    event = service.events().patch(calendarId=Settings.CALENDAR_ID, eventId=event_id, body=body).execute()
    return {"eventId": event["id"], "summary": event.get("summary", ""), "start": new_start.isoformat(), "end": end.isoformat(), "attendees": event.get("attendees", [])}


def cancel_event(event_id: str) -> None:
    service = _calendar_service()
    service.events().delete(calendarId=Settings.CALENDAR_ID, eventId=event_id).execute()
