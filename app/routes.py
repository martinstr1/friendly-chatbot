from __future__ import annotations
import time
import re
import requests
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple
from dateutil import parser as date_parser, tz
from flask import Blueprint, request, jsonify, abort
from .config import Settings
from . import storage
from . import calendar as cal
from . import tasks as ctasks
from .emailer import send_email

bp = Blueprint("routes", __name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
LOCAL_TZ = tz.gettz(Settings.TIMEZONE)

SCHEDULE_KEYWORDS = ["schedule", "book", "set up", "set-up", "setup", "arrange", "appointment", "meeting"]
RESCHEDULE_KEYWORDS = ["reschedule", "move", "change", "update", "shift"]
CANCEL_KEYWORDS = ["cancel", "call off", "drop", "call-off", "calloff"]
STOP_CONVERSATION_PHRASES = ["never mind", "nevermind", "forget it", "ignore that"]


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
EXPLICIT_DATE_PATTERN = re.compile(
    r"\b(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b"
    r"|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|today|tonight|tomorrow|next|this|coming)\b",
    re.IGNORECASE,
)

TIME_COMPONENT_PATTERN = re.compile(
    r"\b(\d{1,2}:\d{2}|\d{1,2}\s?(?:am|pm)|noon|midnight)\b",
    re.IGNORECASE,
)

RELATIVE_HOURS_PATTERN = re.compile(r"\bin\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|h)\b", re.IGNORECASE)
RELATIVE_MINUTES_PATTERN = re.compile(r"\bin\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|m)\b", re.IGNORECASE)
RELATIVE_HALF_HOUR_PATTERN = re.compile(r"\bin\s+half\s+(?:an\s+)?hour\b", re.IGNORECASE)
RELATIVE_SINGLE_HOUR_PATTERN = re.compile(r"\bin\s+(an|a|one)\s+hour\b", re.IGNORECASE)
RELATIVE_WORD_HOURS_PATTERN = re.compile(
    r"\bin\s+(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+hours?\b",
    re.IGNORECASE,
)

DURATION_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m)\b", re.IGNORECASE)
WORD_DURATION_PATTERN = re.compile(
    r"\b(half|an|a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(hours?|hour|hrs?|h|minutes?|mins?|m)\b",
    re.IGNORECASE,
)

STOPWORD_PATTERN = re.compile(
    r"(?i)\b(please|thanks|thank you|schedule|book|set up|set-up|setup|arrange|reschedule|move|change|cancel|appointment|meeting|new|make|"
    r"create|fix|set|put|slot|slot in|plan|organise|organize|could|would|like|help|me|my|for|on|at|the|a|an|to|with|from|about|let|know|if|you|"
    r"hey|hi|hello|thanks|thank|there|just|need|want|please|kindly|any|chance|maybe|could you|can you)\b"
)

DATEWORD_PATTERN = re.compile(
    r"(?i)\b(today|tonight|tomorrow|morning|afternoon|evening|tonite|next|this|coming|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
)

WORD_TO_NUMBER = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _now() -> datetime:
    return datetime.now(LOCAL_TZ)


def _parse_relative_datetime(text: str, reference: datetime) -> Tuple[datetime, Tuple[str, ...]] | None:
    m = RELATIVE_MINUTES_PATTERN.search(text)
    if m:
        minutes = float(m.group(1))
        start = reference + timedelta(minutes=minutes)
        return start, (text[: m.start()], text[m.end() :])

    m = RELATIVE_HOURS_PATTERN.search(text)
    if m:
        hours = float(m.group(1))
        start = reference + timedelta(hours=hours)
        return start, (text[: m.start()], text[m.end() :])

    if RELATIVE_HALF_HOUR_PATTERN.search(text):
        m = RELATIVE_HALF_HOUR_PATTERN.search(text)
        if m:
            start = reference + timedelta(minutes=30)
            return start, (text[: m.start()], text[m.end() :])

    if RELATIVE_SINGLE_HOUR_PATTERN.search(text):
        m = RELATIVE_SINGLE_HOUR_PATTERN.search(text)
        if m:
            start = reference + timedelta(hours=1)
            return start, (text[: m.start()], text[m.end() :])

    m = RELATIVE_WORD_HOURS_PATTERN.search(text)
    if m:
        word = m.group(1).lower()
        hours = WORD_TO_NUMBER.get(word)
        if hours:
            start = reference + timedelta(hours=hours)
            return start, (text[: m.start()], text[m.end() :])

    return None


def _extract_datetime_details(text: str) -> Dict[str, Any]:
    reference = _now()
    relative = _parse_relative_datetime(text, reference)
    if relative:
        start, tokens = relative
        return {
            "start": start,
            "tokens": tokens,
            "has_time": True,
            "has_date": True,
            "explicit_date": True,
            "date": start.date(),
        }

    default = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        parsed, tokens = date_parser.parse(text, fuzzy_with_tokens=True, default=default)
    except (ValueError, OverflowError):
        return {"start": None, "tokens": (), "has_time": False, "has_date": False, "date": None}

    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    parsed = parsed.astimezone(LOCAL_TZ)

    has_time = bool(TIME_COMPONENT_PATTERN.search(text)) or bool(relative)
    explicit_date = bool(EXPLICIT_DATE_PATTERN.search(text))
    has_date = explicit_date
    if has_time and not has_date:
        has_date = True

    if has_time and not has_date and parsed <= reference:
        parsed += timedelta(days=1)

    if has_date and has_time:
        start = parsed
    else:
        start = None

    date_part = parsed.date() if has_date else None

    return {
        "start": start,
        "tokens": tokens,
        "has_time": has_time,
        "has_date": has_date,
        "explicit_date": explicit_date,
        "date": date_part,
        "parsed": parsed,
    }


def _extract_duration(text: str) -> Tuple[int | None, str | None]:
    m = DURATION_PATTERN.search(text)
    if m:
        value = float(m.group(1))
        unit = m.group(2).lower()
        minutes = int(value * 60) if unit.startswith("h") else int(value)
        return max(minutes, 1), m.group(0)

    m = WORD_DURATION_PATTERN.search(text)
    if m:
        word = m.group(1).lower()
        unit = m.group(2).lower()
        if word == "half" and unit.startswith("hour"):
            return 30, m.group(0)
        if word in {"an", "a", "one"}:
            value = 1
        else:
            value = WORD_TO_NUMBER.get(word)
        if value:
            minutes = 60 * value if unit.startswith("h") else value
            return minutes, m.group(0)

    return None, None


def _derive_title(text: str, leftover_tokens: Tuple[str, ...] | None, extra_remove: list[str], existing: str | None) -> str:
    base = "".join(leftover_tokens or ()) if leftover_tokens else text
    for phrase in extra_remove:
        if phrase:
            base = base.replace(phrase, " ")
    base = DATEWORD_PATTERN.sub(" ", base)
    base = re.sub(r"\b\d{1,2}(:\d{2})?\b", " ", base)
    base = re.sub(r"[^A-Za-z0-9'&/@\-\s]", " ", base)
    base = STOPWORD_PATTERN.sub(" ", base)
    cleaned = re.sub(r"\s+", " ", base).strip(" -,:")
    if not cleaned:
        return existing or "Appointment"
    if existing and cleaned.lower() == "appointment":
        return existing
    return cleaned


def _merge_schedule_details(text: str, draft: Dict[str, Any] | None = None) -> Dict[str, Any]:
    draft = draft or {}
    parsed = _extract_datetime_details(text)
    duration, duration_phrase = _extract_duration(text)
    extra_remove = [duration_phrase] if duration_phrase else []
    title = _derive_title(text, parsed.get("tokens"), extra_remove, draft.get("title"))

    start = parsed.get("start")
    has_time = parsed.get("has_time", False)
    stored_date = draft.get("date")
    parsed_dt = parsed.get("parsed")
    explicit_date = parsed.get("explicit_date", False)

    if start and stored_date and parsed_dt and not explicit_date:
        target_date = datetime.fromisoformat(stored_date).date()
        time_component = start.timetz()
        start = datetime.combine(target_date, time_component)
        if not start.tzinfo:
            start = start.replace(tzinfo=LOCAL_TZ)

    if not start and has_time and stored_date and parsed_dt:
        target_date = datetime.fromisoformat(stored_date).date()
        time_component = parsed_dt.timetz()
        start = datetime.combine(target_date, time_component)
        if not start.tzinfo:
            start = start.replace(tzinfo=LOCAL_TZ)

    date_value = parsed.get("date")
    if date_value and (explicit_date or not stored_date):
        date_iso = date_value.isoformat()
    else:
        date_iso = stored_date

    final_duration = duration if duration else draft.get("duration")

    return {
        "title": title,
        "start": start,
        "duration": final_duration,
        "date": date_iso,
    }


def _prompt_for_details(chat_id: int, draft: Dict[str, Any], is_reschedule: bool) -> None:
    title = draft.get("title")
    have_title = title and title != "Appointment"
    if is_reschedule:
        if draft.get("date"):
            _tg_send(chat_id, "Sure — what time should I move it to on that day?")
        else:
            _tg_send(chat_id, "Sure thing! When would you like me to move it to?")
        return

    if draft.get("date"):
        if have_title:
            _tg_send(chat_id, f"Got it, I'll take care of {title}. What time should I use?")
        else:
            _tg_send(chat_id, "Sounds good. What time should I set?")
        return

    if have_title:
        _tg_send(chat_id, f"Happy to help with {title}! When should it happen?")
    else:
        _tg_send(chat_id, "Happy to help! When would you like it?")


def _complete_schedule(chat_id: int, details: Dict[str, Any], is_reschedule: bool) -> bool:
    start_dt = details.get("start")
    duration = details.get("duration") or 30
    title = details.get("title") or "Appointment"

    if not start_dt:
        storage.set_state(
            chat_id,
            {
                "intent": "reschedule" if is_reschedule else "schedule",
                "draft": {
                    "title": title,
                    "duration": duration if duration != 30 else None,
                    "date": details.get("date"),
                },
            },
        )
        _prompt_for_details(chat_id, {
            "title": title,
            "duration": duration if duration != 30 else None,
            "date": details.get("date"),
        }, is_reschedule)
        return True

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=LOCAL_TZ)

    now = _now()
    if start_dt <= now:
        storage.set_state(
            chat_id,
            {
                "intent": "reschedule" if is_reschedule else "schedule",
                "draft": {
                    "title": title,
                    "duration": duration if duration != 30 else None,
                    "date": details.get("date"),
                },
            },
        )
        _tg_send(chat_id, "That time has already passed. Could you share a future time instead?")
        return True

    try:
        if is_reschedule:
            event = storage.get_event(chat_id)
            if not event:
                _tg_send(chat_id, "I couldn't find an appointment to move. Let me know if you'd like me to set up a new one.")
                storage.set_state(chat_id, None)
                return True
            existing_title = event.get("summary") or title
            updated = cal.reschedule_event(event["eventId"], start_dt, duration)
            storage.set_event(chat_id, updated)
            ctasks.delete_tasks(storage.get_task_names(chat_id))
            names = ctasks.schedule_reminders(_base_url(), chat_id, updated)
            storage.set_task_names(chat_id, names)
            storage.set_state(chat_id, None)
            _tg_send(
                chat_id,
                f"All set. I've moved your {existing_title} to {start_dt:%Y-%m-%d at %H:%M} for {duration} minutes.",
            )
        else:
            event = cal.create_event(title, start_dt, duration, None)
            storage.set_event(chat_id, event)
            names = ctasks.schedule_reminders(_base_url(), chat_id, event)
            storage.set_task_names(chat_id, names)
            storage.set_state(chat_id, None)
            _tg_send(
                chat_id,
                f"Great! I've scheduled {title} on {start_dt:%Y-%m-%d at %H:%M} for {duration} minutes.",
            )
            send_email("Appointment scheduled", f"{title} at {start_dt} ({duration}m).")
        return True
    except Exception as e:
        storage.set_state(chat_id, None)
        _tg_send(chat_id, f"Hmm, I wasn't able to finish that: {e}")
        return True


def _handle_cancel(chat_id: int) -> bool:
    try:
        storage.set_state(chat_id, None)
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
    state = storage.get_state(chat_id) or {}

    if state and any(phrase in lower_text for phrase in STOP_CONVERSATION_PHRASES):
        storage.set_state(chat_id, None)
        _tg_send(chat_id, "No problem—I'll hold off on that request.")
        return ("ok", 200)

    if text.startswith("/schedule"):
        storage.set_state(chat_id, None)
        try:
            m = re.match(r"^/schedule\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(\d+)[mM]\s+(.*)$", text)
            if not m:
                raise ValueError("Format: /schedule YYYY-MM-DD HH:MM 30m Title")
            date_s, time_s, dur_s, title = m.groups()
            start_dt = datetime.fromisoformat(f"{date_s}T{time_s}")
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=LOCAL_TZ)
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
        storage.set_state(chat_id, None)
        try:
            m = re.match(r"^/reschedule\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*(\d+)?[mM]?$", text)
            if not m:
                raise ValueError("Format: /reschedule YYYY-MM-DD HH:MM [30m]")
            date_s, time_s, dur_s = m.groups()
            event = storage.get_event(chat_id)
            if not event:
                raise ValueError("No existing event to reschedule.")
            start_dt = datetime.fromisoformat(f"{date_s}T{time_s}")
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=LOCAL_TZ)
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

    if any(word in lower_text for word in CANCEL_KEYWORDS):
        if _handle_cancel(chat_id):
            return ("ok", 200)

    draft_state = dict(state.get("draft") or {}) if isinstance(state, dict) else {}

    if state.get("intent") == "schedule":
        details = _merge_schedule_details(text, draft_state)
        if _complete_schedule(chat_id, details, is_reschedule=False):
            return ("ok", 200)

    if state.get("intent") == "reschedule":
        event = storage.get_event(chat_id)
        base_draft = dict(draft_state)
        if event:
            start_local = datetime.fromisoformat(event["start"])
            if start_local.tzinfo is None:
                start_local = start_local.replace(tzinfo=LOCAL_TZ)
            else:
                start_local = start_local.astimezone(LOCAL_TZ)
            end_local = datetime.fromisoformat(event["end"])
            if end_local.tzinfo is None:
                end_local = end_local.replace(tzinfo=LOCAL_TZ)
            else:
                end_local = end_local.astimezone(LOCAL_TZ)
            base_draft.setdefault("title", event.get("summary") or "Appointment")
            base_draft.setdefault("duration", int((end_local - start_local).total_seconds() // 60))
            base_draft.setdefault("date", start_local.date().isoformat())
        details = _merge_schedule_details(text, base_draft)
        if _complete_schedule(chat_id, details, is_reschedule=True):
            return ("ok", 200)

    if any(word in lower_text for word in RESCHEDULE_KEYWORDS):
        event = storage.get_event(chat_id)
        if not event:
            _tg_send(chat_id, "I couldn't find an appointment to move, but I'm happy to set up a new one if you'd like.")
            storage.set_state(chat_id, None)
            return ("ok", 200)
        start_local = datetime.fromisoformat(event["start"])
        if start_local.tzinfo is None:
            start_local = start_local.replace(tzinfo=LOCAL_TZ)
        else:
            start_local = start_local.astimezone(LOCAL_TZ)
        end_local = datetime.fromisoformat(event["end"])
        if end_local.tzinfo is None:
            end_local = end_local.replace(tzinfo=LOCAL_TZ)
        else:
            end_local = end_local.astimezone(LOCAL_TZ)
        base_draft = {
            "title": event.get("summary") or "Appointment",
            "duration": int((end_local - start_local).total_seconds() // 60),
            "date": start_local.date().isoformat(),
        }
        details = _merge_schedule_details(text, base_draft)
        if _complete_schedule(chat_id, details, is_reschedule=True):
            return ("ok", 200)

    if any(word in lower_text for word in SCHEDULE_KEYWORDS):
        details = _merge_schedule_details(text)
        if _complete_schedule(chat_id, details, is_reschedule=False):
            return ("ok", 200)

    if text:
        _tg_send(chat_id, "Thanks for the note! If you need to schedule, move, or cancel an appointment, just let me know how I can help.")
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

