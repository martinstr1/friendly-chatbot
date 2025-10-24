from __future__ import annotations
import time
import re
import requests
from datetime import datetime
from dateutil import parser as du_parser, tz as dtz
from flask import Blueprint, request, jsonify, abort
from .config import Settings
from . import storage
from . import calendar as cal
from . import tasks as ctasks
from .emailer import send_email

bp = Blueprint("routes", __name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

LOCAL_TZ = dtz.gettz(Settings.TIMEZONE)
TIME_HINT_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b|\b(?:1[0-2]|0?[1-9])\s?(?:am|pm)\b|\b(?:noon|midnight)\b", re.IGNORECASE)
HOUR_ONLY_RE = re.compile(r"\bat\s+(1[0-2]|0?[1-9]|2[0-3])\b", re.IGNORECASE)
DATE_HINT_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b(?:today|tomorrow|tonight)\b"
    r"|\b(?:next|this)\s+(?:week|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
DURATION_RE = re.compile(r"\b(\d+)\s?(minutes?|mins?|m|hours?|hrs?|h)\b", re.IGNORECASE)
TIME_CLEAN_RE = re.compile(r"\b(?:at\s+)?(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s?(?:am|pm)?\b|\b(?:noon|midnight)\b", re.IGNORECASE)
DATE_CLEAN_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b(?:today|tomorrow|tonight|yesterday)\b"
    r"|\b(?:next|this)\s+(?:week|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+\d{1,2}(?:st|nd|rd|th)?)?\b",
    re.IGNORECASE,
)
TITLE_STOPWORDS = {
    "schedule",
    "scheduled",
    "scheduling",
    "book",
    "set",
    "setup",
    "set-up",
    "arrange",
    "appointment",
    "please",
    "could",
    "would",
    "like",
    "help",
    "me",
    "to",
    "for",
    "on",
    "at",
    "the",
    "a",
    "an",
    "new",
    "my",
    "with",
    "thanks",
    "thank",
    "you",
    "hey",
    "hi",
    "hello",
    "need",
    "make",
    "add",
    "create",
    "please",
    "let",
    "know",
    "about",
    "and",
    "can",
    "we",
    "it",
    "is",
    "in",
    "for",
}
CANCEL_CONTEXT_PHRASES = ("never mind", "nevermind", "forget it", "don't worry about it")


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


def _infer_title(text: str) -> str:
    cleaned = DATE_CLEAN_RE.sub(" ", text)
    cleaned = TIME_CLEAN_RE.sub(" ", cleaned)
    cleaned = DURATION_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[^\w\s'-]", " ", cleaned)
    tokens = [tok for tok in re.split(r"\s+", cleaned) if tok]
    filtered = [tok for tok in tokens if tok.lower() not in TITLE_STOPWORDS]
    if not filtered:
        return "Appointment"
    candidate = " ".join(filtered).strip()
    return candidate or "Appointment"


def _extract_message_details(text: str) -> dict[str, object]:
    details: dict[str, object] = {"date": None, "time": None, "duration": None, "title": None}
    lower = text.lower()

    dur_match = DURATION_RE.search(lower)
    if dur_match:
        value = int(dur_match.group(1))
        unit = dur_match.group(2).lower()
        details["duration"] = value * 60 if unit.startswith("h") else value

    title = _infer_title(text)
    if title and title.lower() != "appointment":
        details["title"] = title

    local_tz = LOCAL_TZ or dtz.UTC
    now_local = datetime.now(local_tz)
    default_dt = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    parsed_dt = None
    try:
        parsed_dt = du_parser.parse(text, fuzzy=True, default=default_dt)
    except (ValueError, OverflowError):
        parsed_dt = None

    has_date = False
    has_time = False
    if parsed_dt:
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=local_tz)
        else:
            parsed_dt = parsed_dt.astimezone(local_tz)
        if parsed_dt.date() != default_dt.date():
            has_date = True
        if parsed_dt.time() != default_dt.time():
            has_time = True

    if not has_date and DATE_HINT_RE.search(lower):
        has_date = True
    if not has_time and (TIME_HINT_RE.search(lower) or HOUR_ONLY_RE.search(lower)):
        has_time = True

    if parsed_dt and has_date:
        details["date"] = parsed_dt.date().isoformat()
    if parsed_dt and has_time:
        details["time"] = parsed_dt.strftime("%H:%M")

    return details


def _prompt_for_missing(intent: str, missing: list[str]) -> str:
    if intent == "reschedule":
        if "date" in missing and "time" in missing:
            return "Sure, I can move it. What day and time should I switch it to?"
        if "date" in missing:
            return "Happy to reschedule. Which day works better for you?"
        if "time" in missing:
            return "Got it. And what time should we move it to?"
    else:
        if "date" in missing and "time" in missing:
            return "Absolutely! What day and time would you like me to set up?"
        if "date" in missing:
            return "Sounds good. Which day should I put it on?"
        if "time" in missing:
            return "Great. What time should I book it for?"
    return "Happy to help. Just share the details and I'll take care of it."


def _combine_start(draft: dict[str, object]) -> datetime | None:
    date_s = draft.get("date")
    time_s = draft.get("time")
    if not (isinstance(date_s, str) and isinstance(time_s, str)):
        return None
    try:
        return datetime.fromisoformat(f"{date_s}T{time_s}")
    except ValueError:
        return None


def _ensure_context(intent: str, ctx: dict[str, object] | None) -> dict[str, object]:
    if not ctx or ctx.get("intent") != intent:
        return {"intent": intent, "draft": {}}
    ctx.setdefault("draft", {})
    return ctx


def _handle_scheduling(chat_id: int, text: str, ctx: dict[str, object] | None, is_reschedule: bool = False) -> bool:
    intent = "reschedule" if is_reschedule else "schedule"
    working_ctx = _ensure_context(intent, ctx)
    draft: dict[str, object] = working_ctx.setdefault("draft", {})

    details = _extract_message_details(text)
    if details.get("date"):
        draft["date"] = details["date"]
    if details.get("time"):
        draft["time"] = details["time"]
    if details.get("duration"):
        draft["duration"] = details["duration"]
    if details.get("title") and not draft.get("title"):
        draft["title"] = details["title"]

    missing: list[str] = []
    if not draft.get("date"):
        missing.append("date")
    if not draft.get("time"):
        missing.append("time")

    if missing:
        storage.set_context(chat_id, working_ctx)
        _tg_send(chat_id, _prompt_for_missing(intent, missing))
        return True

    start_dt = _combine_start(draft)
    if not start_dt:
        storage.set_context(chat_id, working_ctx)
        _tg_send(chat_id, "I couldn't quite make out that time. Could you share it again?")
        return True

    duration = int(draft.get("duration") or 30)
    title = (draft.get("title") or "Appointment")

    try:
        if is_reschedule:
            event = storage.get_event(chat_id)
            if not event:
                storage.set_context(chat_id, None)
                _tg_send(chat_id, "I couldn't find another appointment to move right now.")
                return True
            updated = cal.reschedule_event(event["eventId"], start_dt, duration)
            storage.set_event(chat_id, updated)
            ctasks.delete_tasks(storage.get_task_names(chat_id))
            names = ctasks.schedule_reminders(_base_url(), chat_id, updated)
            storage.set_task_names(chat_id, names)
            event_title = updated.get("summary") or event.get("summary") or title
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
        storage.set_context(chat_id, None)
        return True
    except Exception as e:
        storage.set_context(chat_id, None)
        _tg_send(chat_id, f"Hmm, I wasn't able to finish that: {e}")
        return True


def _handle_cancel(chat_id: int) -> bool:
    try:
        event = storage.get_event(chat_id)
        if event:
            cal.cancel_event(event["eventId"])
            ctasks.delete_tasks(storage.get_task_names(chat_id))
            storage.set_event(chat_id, None)
        storage.set_context(chat_id, None)
        _tg_send(chat_id, "Consider it done. Your appointment is cancelled.")
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
        storage.set_context(chat_id, None)
        _tg_send(chat_id, "Commands: /schedule <YYYY-MM-DD HH:MM> <duration m> <title>; /reschedule <YYYY-MM-DD HH:MM>; /cancel")
        return ("ok", 200)

    lower_text = text.lower()
    ctx = storage.get_context(chat_id)

    if text and ctx and any(phrase in lower_text for phrase in CANCEL_CONTEXT_PHRASES):
        storage.set_context(chat_id, None)
        _tg_send(chat_id, "No worries, I'll leave that for now. Just let me know if you'd like to pick it back up.")
        return ("ok", 200)

    if text.startswith("/schedule"):
        storage.set_context(chat_id, None)
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

    if text.startswith("/reschedule"):
        storage.set_context(chat_id, None)
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
        storage.set_context(chat_id, None)
        _handle_cancel(chat_id)
        return ("ok", 200)

    if text and ctx and ctx.get("intent") in {"schedule", "reschedule"}:
        if _handle_scheduling(chat_id, text, ctx, is_reschedule=(ctx.get("intent") == "reschedule")):
            return ("ok", 200)

    schedule_keywords = ["schedule", "book", "set up", "set-up", "setup", "arrange", "appointment"]
    reschedule_keywords = ["reschedule", "move", "change", "update"]

    if text and any(word in lower_text for word in schedule_keywords):
        if _handle_scheduling(chat_id, text, ctx, is_reschedule=False):
            return ("ok", 200)
        _tg_send(
            chat_id,
            "I'd love to help with that. Share the day and time you'd like, and I'll get it scheduled.",
        )
        return ("ok", 200)

    if text and any(word in lower_text for word in reschedule_keywords):
        if _handle_scheduling(chat_id, text, ctx, is_reschedule=True):
            return ("ok", 200)
        _tg_send(
            chat_id,
            "Sure—just let me know the new day and time and I'll move your appointment.",
        )
        return ("ok", 200)

    if text and any(word in lower_text for word in ["cancel", "call off", "drop"]):
        if _handle_cancel(chat_id):
            return ("ok", 200)

    if text and _handle_scheduling(chat_id, text, ctx, is_reschedule=False):
        return ("ok", 200)

    if text:
        _tg_send(chat_id, "Thanks for reaching out! When you're ready, just share the day and time and I'll book the appointment.")
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

