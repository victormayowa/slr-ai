"""Sending email: password resets, email verification, and notifications.

EMAIL_BACKEND chooses how:
- "smtp" sends through SMTP_HOST (the default when SMTP_HOST and SMTP_FROM are set);
- "console" writes each message to the log, and to EMAIL_OUTBOX_DIR when set, so links can be followed in development
  (the default outside production when SMTP isn't set; refused in production);
- "disabled" sends nothing (the default in production when SMTP isn't set; password resets then can't be delivered).

SMTP credentials are never logged.
"""

import logging
import os
import smtplib
import time
from collections import deque
from email.message import EmailMessage
from pathlib import Path

logger = logging.getLogger(__name__)

BACKENDS = ("smtp", "console", "disabled")
# The most recent console messages, newest last, for development tools and tests.
OUTBOX: deque[dict[str, str]] = deque(maxlen=50)


class EmailError(Exception):
    """A message couldn't be sent. The message is safe to show users."""


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_FROM", "").strip())


def backend() -> str:
    chosen = os.getenv("EMAIL_BACKEND", "").strip().lower()
    if chosen in BACKENDS:
        return chosen
    if smtp_configured():
        return "smtp"
    return "disabled" if os.getenv("APP_ENV", "development") == "production" else "console"


def delivers() -> bool:
    """Whether messages reach anyone (a mailbox, or the developer's console)."""
    return backend() != "disabled"


def send(to: str, subject: str, text: str) -> None:
    chosen = backend()
    if chosen == "disabled":
        logger.warning("Email is disabled, so a message to a user was not sent: %s", subject)
        return
    if chosen == "console":
        _to_console(to, subject, text)
        return
    message = EmailMessage()
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            if os.getenv("SMTP_STARTTLS", "true").lower() != "false":
                server.starttls()
            if os.getenv("SMTP_USERNAME"):
                server.login(os.environ["SMTP_USERNAME"], os.getenv("SMTP_PASSWORD", ""))
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("Sending email through %s failed: %s", host, type(exc).__name__)
        raise EmailError("The email couldn't be sent. Try again later.") from exc


def _to_console(to: str, subject: str, text: str) -> None:
    OUTBOX.append({"to": to, "subject": subject, "text": text})
    logger.info("Email (console backend) to %s: %s\n%s", to, subject, text)
    folder = os.getenv("EMAIL_OUTBOX_DIR", "").strip()
    if folder:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        safe_to = "".join(character if character.isalnum() else "_" for character in to)[:60]
        (path / f"{time.time_ns()}-{safe_to}.txt").write_text(f"To: {to}\nSubject: {subject}\n\n{text}\n")
