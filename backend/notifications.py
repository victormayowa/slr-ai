"""In-app notifications, @mentions, and email delivery.

Email is sent by the worker (deliver_pending_emails) through emailer.py; when email is disabled, notifications stay
in the app.
"""

import logging
import os
import re
from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

import emailer
import models

logger = logging.getLogger(__name__)

MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
EMAIL_WINDOW = timedelta(days=2)


def app_url(link: str = "") -> str:
    return os.getenv("APP_URL", "http://localhost:5173").rstrip("/") + link


def mentioned_users(text: str, members: Iterable[models.ProjectMember]) -> list[int]:
    """Members mentioned as @email in the text (their account or institutional email)."""
    emails = {m.lower() for m in MENTION.findall(text)}
    found = []
    for member in members:
        user = member.user
        if user.email.lower() in emails or (user.institutional_email or "").lower() in emails:
            found.append(user.id)
    return sorted(set(found))


def notify(
    db: Session, *, user_id: int, project_id: int | None, kind: str, title: str, body: str = "", link: str = ""
) -> models.Notification:
    notification = models.Notification(
        user_id=user_id, project_id=project_id, kind=kind, title=title[:300], body=body[:5000], link=link[:500]
    )
    db.add(notification)
    return notification


def notify_members(
    db: Session,
    project: models.Project,
    *,
    kind: str,
    title: str,
    body: str = "",
    link: str = "",
    exclude: Iterable[int] = (),
    roles: Iterable[str] | None = None,
) -> None:
    skipped = set(exclude)
    allowed = set(roles) if roles is not None else None
    for member in project.members:
        if member.user_id in skipped or (allowed is not None and member.role not in allowed):
            continue
        notify(db, user_id=member.user_id, project_id=project.id, kind=kind, title=title, body=body, link=link)


def smtp_configured() -> bool:
    """Whether notification emails are delivered anywhere (SMTP, or the development console)."""
    return emailer.delivers()


def send_email(to: str, subject: str, text: str) -> None:
    emailer.send(to, subject, text)


def deliver_pending_emails(db: Session, limit: int = 200) -> int:
    """Email recent unread notifications to people who allow email. Returns how many were sent."""
    if not smtp_configured():
        return 0
    cutoff = models.utcnow() - EMAIL_WINDOW
    pending = db.scalars(
        select(models.Notification)
        .join(models.User, models.User.id == models.Notification.user_id)
        .where(
            models.Notification.emailed_at.is_(None),
            models.Notification.read_at.is_(None),
            models.Notification.created_at >= cutoff,
            models.User.email_notifications.is_(True),
            models.User.is_active.is_(True),
        )
        .order_by(models.Notification.id)
        .limit(limit)
    ).all()
    sent = 0
    for notification in pending:
        body = f"{notification.body}\n\n{app_url(notification.link)}" if notification.link else notification.body
        try:
            send_email(notification.user.email, f"OmniReview: {notification.title}", body.strip())
        except emailer.EmailError:
            logger.warning("Emailing notification %s failed", notification.id)
            continue
        notification.emailed_at = models.utcnow()
        sent += 1
    db.commit()
    return sent


def remind_due_tasks(db: Session) -> int:
    """Notify assignees of open tasks due tomorrow or overdue, once per task."""
    today = models.utcnow().date()
    tasks = db.scalars(
        select(models.Task).where(
            models.Task.status != "done",
            models.Task.assignee_id.is_not(None),
            models.Task.due_on.is_not(None),
            models.Task.due_on <= today + timedelta(days=1),
            models.Task.due_reminder_sent_at.is_(None),
        )
    ).all()
    for task in tasks:
        when = "overdue" if task.due_on and task.due_on < today else "due soon"
        notify(
            db,
            user_id=task.assignee_id,  # type: ignore[arg-type]
            project_id=task.project_id,
            kind="task_due",
            title=f"Task {when}: {task.title}",
            body=f"Due {task.due_on.isoformat() if task.due_on else ''}",
            link=f"/projects/{task.project_id}/team",
        )
        task.due_reminder_sent_at = models.utcnow()
    db.commit()
    return len(tasks)
