from __future__ import annotations
import time
import re
import requests
from datetime import datetime
from flask import Blueprint, request, jsonify, abort
from .config import Settings
from . import storage
from . import calendar as cal
from . import tasks as ctasks
from .emailer import send_email

bp = Blueprint("routes", __name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def _tg_send(chat_id: int, text: str):
    url = TELEGRAM_API.format(token=Settings.TELEGRAM_BOT_TOKEN, method="sendMessage")
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception:
        pass


def _base_url() -> str:
    raw = (Settings.BASE_URL or request.url_root.rstrip("/"))
    if raw.startswith("http://"):
        raw = "https://" + raw[len("http://"):]
    return raw


def _extract_datetime_details(text: str):
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    time_match = re.search(r"\b(\d{1,2}:\d{2})\b", text)
    duration_match = re.search(r"\b(\d+)\s?(minutes?|mins?|m|hours?|hrs?|h)\b", text, re.IGNORECASE)

    if not (date_match and time_match):
        return None

    date_s = date_match.group(1)
    time_s = time_match.group(1)

    if len(time_s) == 4:
        time_s = f"0{time_s}"

    duration = None
    if duration_match:
        value = int(duration_match.group(1))
        unit = duration_match.group(2).lower()
        duration = value * 60 if unit.startswith("h") else value

    consumed = [date_match.span(), time_match.span()]
    if duration_match:
        consumed.append(duration_match.span())

    return date_s, time_s, duration, consumed


def _extract_title(text: str, consumed_spans: list[tuple[int, int]] | None) -> str:
    if not consumed_spans:
        return "Appointment"

    consumed_spans = sorted(consumed_spans)
    pieces = []
    cursor = 0
    for start, end in consumed_spans:
        if cursor < start:
            pieces.append(text[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(text):
        pieces.append(text[cursor:])

    raw = " ".join(pieces)
    raw = re.sub(r"(?i)\b(schedule|book|set up|arrange|reschedule|move|change|cancel|appointment|please|could|would|like|help|me|to|for|on|at|the|a|an|new|my)\b", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw or "Appointment"


def _handle_scheduling(chat_id: int, text: str, is_reschedule: bool = False) -> bool:
    details = _extract_datetime_details(text)
    if not details:
        return False

    date_s, time_s, duration, consumed = details
    try:
        start_dt = datetime.fromisoformat(f"{date_s}T{time_s}")
    except ValueError:
        return False

    title = _extract_title(text, consumed)
    if not duration:
        duration = 30

    try:
        if is_reschedule:
            event = storage.get_event(chat_id)
            if not event:
                raise ValueError("I couldn't find an appointment to move.")
            updated = cal.reschedule_event(event["eventId"], start_dt, duration)
            storage.set_event(chat_id, updated)
            ctasks.delete_tasks(storage.get_task_names(chat_id))
            names = ctasks.schedule_reminders(_base_url(), chat_id, updated)
            storage.set_task_names(chat_id, names)
            event_title = (event.get("summary") or title or "Appointment")
            _tg_send(
                chat_id,
                f"All set. I've moved your {event_title} to {start_dt:%Y-%m-%d at %H:%M} for {duration} minutes.",
            )
        else:
            event = cal.create_event(title, start_dt, duration, None)
            storage.set_event(chat_id, event)
            names = ctasks.schedule_reminders(_base_url(), chat_id, event)
            storage.set_task_names(chat_id, names)
            _tg_send(
                chat_id,
                f"Great! I've scheduled {title} on {start_dt:%Y-%m-%d at %H:%M} for {duration} minutes.",
            )
            send_email("Appointment scheduled", f"{title} at {start_dt} ({duration}m).")
        return True
    except Exception as e:
        _tg_send(chat_id, f"Hmm, I wasn't able to finish that: {e}")
        return True


def _handle_cancel(chat_id: int) -> bool:
    try:
        event = storage.get_event(chat_id)
        if event:
            cal.cancel_event(event["eventId"])
            ctasks.delete_tasks(storage.get_task_names(chat_id))
            storage.set_event(chat_id, None)
        _tg_send(chat_id, "Consider it done—your appointment is cancelled.")
        return True
    except Exception as e:
        _tg_send(chat_id, f"I ran into an issue cancelling that: {e}")
        return True

    return False


@bp.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    secret = Settings.TELEGRAM_WEBHOOK_SECRET
    if secret:
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if provided != secret:
            abort(401)

    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return ("ok", 200)

    # Voice/audio guard
    if "voice" in message or "audio" in message:
        _tg_send(chat_id, "Please send a text message.")
        return ("ok", 200)

    text = (message.get("text") or "").strip()
    ts = int(message.get("date", time.time()))
    if text:
        storage.append_message(chat_id, "user", text, ts)

    if text.startswith("/help"):
        _tg_send(chat_id, "Commands: /schedule <YYYY-MM-DD HH:MM> <duration m> <title>; /reschedule <YYYY-MM-DD HH:MM>; /cancel")
        return ("ok", 200)

    lower_text = text.lower()

    if text.startswith("/schedule"):
        try:
            m = re.match(r"^/schedule\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(\d+)[mM]\s+(.*)$", text)
            if not m:
                raise ValueError("Format: /schedule YYYY-MM-DD HH:MM 30m Title")
            date_s, time_s, dur_s, title = m.groups()
            start_dt = datetime.fromisoformat(f"{date_s}T{time_s}")
            duration = int(dur_s)
            event = cal.create_event(title, start_dt, duration, None)
            storage.set_event(chat_id, event)

            names = ctasks.schedule_reminders(_base_url(), chat_id, event)
            storage.set_task_names(chat_id, names)

            _tg_send(chat_id, f"All done! I've scheduled {title} on {start_dt:%Y-%m-%d at %H:%M} for {duration} minutes.")
            send_email("Appointment scheduled", f"{title} at {start_dt} ({duration}m).")
        except Exception as e:
            _tg_send(chat_id, f"Failed to schedule: {e}")
        return ("ok", 200)

    schedule_keywords = ["schedule", "book", "set up", "set-up", "setup", "arrange"]
    reschedule_keywords = ["reschedule", "move", "change", "update"]

    if any(word in lower_text for word in schedule_keywords):
        if _handle_scheduling(chat_id, text, is_reschedule=False):
            return ("ok", 200)
        _tg_send(
            chat_id,
            "I'd love to help with that! Please let me know the date and time (YYYY-MM-DD HH:MM) "
            "and, if you like, the duration for the appointment.",
        )
        return ("ok", 200)

    if any(word in lower_text for word in reschedule_keywords):
        if _handle_scheduling(chat_id, text, is_reschedule=True):
            return ("ok", 200)
        _tg_send(
            chat_id,
            "Sure thing—just share the new date and time (YYYY-MM-DD HH:MM) and I'll move the appointment. "
            "You can include a new duration too if it needs to change.",
        )
        return ("ok", 200)

    if text.startswith("/reschedule"):
        try:
            m = re.match(r"^/reschedule\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*(\d+)?[mM]?$", text)
            if not m:
                raise ValueError("Format: /reschedule YYYY-MM-DD HH:MM [30m]")
            date_s, time_s, dur_s = m.groups()
            event = storage.get_event(chat_id)
            if not event:
                raise ValueError("No existing event to reschedule.")
            start_dt = datetime.fromisoformat(f"{date_s}T{time_s}")
            duration = int(dur_s) if dur_s else int((datetime.fromisoformat(event["end"]) - datetime.fromisoformat(event["start"])).total_seconds() // 60)
            updated = cal.reschedule_event(event["eventId"], start_dt, duration)
            storage.set_event(chat_id, updated)

            ctasks.delete_tasks(storage.get_task_names(chat_id))
            names = ctasks.schedule_reminders(_base_url(), chat_id, updated)
            storage.set_task_names(chat_id, names)

            _tg_send(chat_id, f"Sure thing. Your appointment is now set for {start_dt:%Y-%m-%d at %H:%M} and will last {duration} minutes.")
        except Exception as e:
            _tg_send(chat_id, f"Failed to reschedule: {e}")
        return ("ok", 200)

    if text.startswith("/cancel"):
        _handle_cancel(chat_id)
        return ("ok", 200)

    if any(word in lower_text for word in ["cancel", "call off", "drop"]):
        if _handle_cancel(chat_id):
            return ("ok", 200)

    if _handle_scheduling(chat_id, text, is_reschedule=False):
        return ("ok", 200)

    if text:
        _tg_send(chat_id, "Thanks for the message! If you'd like me to set up a new appointment, just let me know and I'll be glad to help.")
    return ("ok", 200)


@bp.route("/tasks/send-reminder", methods=["POST"])
def send_reminder():
    body = request.get_json(silent=True) or {}
    chat_id = body.get("chat_id")
    reminder_type = body.get("type")
    event = body.get("event") or {}
    title = event.get("summary", "Appointment")
    start = event.get("start", "")
    if not chat_id:
        return jsonify({"ok": False}), 400

    if reminder_type == "day_before":
        _tg_send(chat_id, f"Reminder: {title} tomorrow at {start}.")
        send_email("Day-before reminder", f"{title} is scheduled at {start}.")
    else:
        _tg_send(chat_id, f"Reminder: {title} starts in 1 hour at {start}.")
    return jsonify({"ok": True}), 200

