"""
Sends a plain-text email notification when a new enquiry comes in, using a standard SMTP
account (e.g. a free Gmail account with an App Password — no paid email API needed).
Fails silently (logs a warning) if SMTP isn't configured or the send fails — a notification
email should never block the enquiry itself from being saved.
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText

logger = logging.getLogger("pagecraft")


def send_enquiry_notification(to_email: str, business_name: str, fields: dict) -> bool:
    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")

    if not (host and port and user and password and to_email):
        logger.info("SMTP not configured or no notify email set — skipping email, enquiry still saved.")
        return False

    lines = "\n".join(f"{k}: {v}" for k, v in fields.items())
    body = f"New enquiry for {business_name}:\n\n{lines}"

    msg = MIMEText(body)
    msg["Subject"] = f"New enquiry — {business_name}"
    msg["From"] = user
    msg["To"] = to_email

    try:
        with smtplib.SMTP(host, int(port), timeout=15) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [to_email], msg.as_string())
        return True
    except Exception:
        logger.exception("Failed to send enquiry notification email (non-fatal, enquiry still saved)")
        return False
