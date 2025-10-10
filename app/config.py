from __future__ import annotations
import os


def env(key: str, default: str | None = None, required: bool = False) -> str:
    val = os.getenv(key, default)
    if required and (val is None or val == ""):
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val or ""


class Settings:
    # Core
    PROJECT_ID = env("GC_PROJECT_ID", required=True)
    REGION = env("GC_REGION", "southamerica-west1")
    TASKS_REGION = env("GC_TASKS_REGION", "southamerica-east1")
    FIRESTORE_COLLECTION = env("FIRESTORE_COLLECTION", "chats")
    TIMEZONE = env("TIMEZONE", "America/Lima")

    # Telegram
    TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", required=True)
    TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", "").strip()

    # Calendar
    CALENDAR_ID = env("CALENDAR_ID", required=True)

    # Email
    EMAIL_FROM = env("EMAIL_FROM", required=True)
    EMAIL_TO_DEFAULT = env("EMAIL_TO_DEFAULT", required=True)
    SMTP_HOST = env("SMTP_HOST", "")
    SMTP_PORT = int(env("SMTP_PORT", "587") or "587")
    SMTP_USER = env("SMTP_USER", "")
    SMTP_PASSWORD = env("SMTP_PASSWORD", "")

    # Service URL (set after first deploy)
    BASE_URL = env("BASE_URL", "")
