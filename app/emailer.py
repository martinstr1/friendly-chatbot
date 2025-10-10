from __future__ import annotations
import smtplib
from email.message import EmailMessage
from typing import Optional
from .config import Settings


def send_email(subject: str, body: str, to_email: Optional[str] = None) -> None:
    to_addr = to_email or Settings.EMAIL_TO_DEFAULT
    msg = EmailMessage()
    msg["From"] = Settings.EMAIL_FROM
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    if not (Settings.SMTP_HOST and Settings.SMTP_USER and Settings.SMTP_PASSWORD):
        return  # SMTP not configured

    with smtplib.SMTP(Settings.SMTP_HOST, Settings.SMTP_PORT) as s:
        s.starttls()
        s.login(Settings.SMTP_USER, Settings.SMTP_PASSWORD)
        s.send_message(msg)
