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

            _tg_send(chat_id, f"Scheduled: {title} at {start_dt} for {duration}m.")
            send_email("Appointment scheduled", f"{title} at {start_dt} ({duration}m).")
        except Exception as e:
            _tg_send(chat_id, f"Failed to schedule: {e}")
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

            _tg_send(chat_id, f"Rescheduled to {start_dt} for {duration}m.")
        except Exception as e:
            _tg_send(chat_id, f"Failed to reschedule: {e}")
        return ("ok", 200)

    if text.startswith("/cancel"):
        try:
            event = storage.get_event(chat_id)
            if event:
                cal.cancel_event(event["eventId"])
                ctasks.delete_tasks(storage.get_task_names(chat_id))
                storage.set_event(chat_id, None)
            _tg_send(chat_id, "Cancelled.")
        except Exception as e:
            _tg_send(chat_id, f"Failed to cancel: {e}")
        return ("ok", 200)

    if text:
        _tg_send(chat_id, "Got it. I'm here to help. Use /help for commands.")
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

