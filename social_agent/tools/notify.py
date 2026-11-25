"""Alerts to the owner outside the terminal (plan: "push or email alert").

  Telegram when TELEGRAM_BOT_TOKEN and the owner's chat ID are set (the Owner app on the phone).
  Webhook  when ALERT_WEBHOOK_URL is set (Slack, Discord or Teams incoming webhook).
  Email    when SMTP_HOST and ALERT_EMAIL_TO are set.
With neither, alerts show only in the terminal and in alerts.json.
"""
import smtplib
from email.message import EmailMessage

from .. import config
from . import telegram
from .http import ServiceError, request


def send(business: str, text: str, phone: bool = True) -> list[str]:
    """Send an alert. Returns the channels that worked. Never raises.
    phone=False: skip Telegram (for example when the phone already got a preview)."""
    sent, message = [], f"[{business}] {text}"
    if phone and telegram.enabled(business):
        try:
            telegram.send_text(config.telegram_chat_id(business), "🔔 " + message)
            sent.append("telegram")
        except ServiceError:
            pass
    if config.ALERT_WEBHOOK_URL:
        try:
            # "text" works for Slack and Teams; "content" for Discord.
            request("POST", config.ALERT_WEBHOOK_URL, service="Alert webhook", timeout=15,
                    json={"text": message, "content": message})
            sent.append("webhook")
        except ServiceError:
            pass
    if config.SMTP_HOST and config.ALERT_EMAIL_TO:
        try:
            msg = EmailMessage()
            msg["Subject"] = f"Social media team alert: {business}"
            msg["From"] = config.ALERT_EMAIL_FROM or config.SMTP_USER
            msg["To"] = config.ALERT_EMAIL_TO
            msg.set_content(message)
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
                smtp.starttls()
                if config.SMTP_USER:
                    smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
                smtp.send_message(msg)
            sent.append("email")
        except (OSError, smtplib.SMTPException):
            pass
    return sent
