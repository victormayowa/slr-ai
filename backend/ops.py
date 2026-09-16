"""Operations: readiness and health checks, heartbeats from background processes, the daily operations report, and
housekeeping for subscriptions.

/readyz runs only the checks the API can't serve without (database, migrations, Redis when configured, writable
storage), so a load balancer or Docker takes an unready instance out of service. The administrator system page runs
all of them, including ones that only warn (backups, the worker, R, email).
"""

import logging
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.orm import Session

import emailer
import entitlements
import models
import notifications
from database import engine

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent
WORKER_STALE_AFTER = timedelta(minutes=5)
BACKUP_STALE_AFTER = timedelta(hours=26)
RESTORE_DRILL_STALE_AFTER = timedelta(days=35)
DISK_WARN_PERCENT = 85
DISK_FAIL_PERCENT = 95
SUBSCRIPTION_GRACE = timedelta(days=int(os.getenv("BILLING_GRACE_DAYS", "7")))


@dataclass
class Check:
    name: str
    # "ok", "warn", or "fail"
    status: str
    detail: str

    def out(self) -> dict[str, str]:
        return asdict(self)


def heartbeat(db: Session, name: str, detail: dict[str, Any] | None = None) -> None:
    row = db.scalar(select(models.OpsHeartbeat).where(models.OpsHeartbeat.name == name))
    if row is None:
        row = models.OpsHeartbeat(name=name)
        db.add(row)
    row.last_seen_at = models.utcnow()
    row.detail = detail or {}
    db.commit()


def _age_check(
    db: Session, name: str, label: str, stale_after: timedelta, missing: str, fail_when_stale: bool
) -> Check:
    row = db.scalar(select(models.OpsHeartbeat).where(models.OpsHeartbeat.name == name))
    if row is None:
        return Check(name, "fail" if fail_when_stale else "warn", missing)
    age = models.utcnow() - row.last_seen_at
    if age > stale_after:
        hours = age.total_seconds() / 3600
        return Check(name, "fail" if fail_when_stale else "warn", f"{label} last reported {hours:.1f} hours ago")
    return Check(name, "ok", f"{label} reported {int(age.total_seconds() // 60)} minutes ago")


def check_database() -> Check:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database check failed")
        return Check("database", "fail", "The database can't be reached")
    return Check("database", "ok", "Connected")


def check_migrations() -> Check:
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    try:
        script = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))
        heads = set(script.get_heads())
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
    except Exception:
        logger.exception("Migration check failed")
        return Check("migrations", "fail", "The database schema version couldn't be read")
    if current != heads:
        return Check("migrations", "fail", f"The schema is at {sorted(current)}; run alembic upgrade head")
    return Check("migrations", "ok", f"At {', '.join(sorted(heads))}")


def check_redis() -> Check | None:
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return None
    import redis

    try:
        redis.Redis.from_url(url, socket_timeout=2).ping()
    except redis.RedisError:
        return Check("redis", "fail", "Redis can't be reached, so background jobs and shared rate limits stop")
    return Check("redis", "ok", "Connected")


def check_storage() -> Check:
    from storage import BACKEND_DIR as STORAGE_BACKEND_DIR

    root = Path(os.getenv("DOCUMENT_STORAGE_DIR") or STORAGE_BACKEND_DIR / "storage" / "documents")
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, prefix=".readiness-", delete=True) as handle:
            handle.write(b"ok")
        usage = shutil.disk_usage(root)
    except OSError:
        return Check("storage", "fail", f"Document storage at {root} isn't writable")
    percent = round(usage.used / usage.total * 100)
    if percent >= DISK_FAIL_PERCENT:
        return Check("storage", "fail", f"The disk holding documents is {percent}% full")
    if percent >= DISK_WARN_PERCENT:
        return Check("storage", "warn", f"The disk holding documents is {percent}% full")
    return Check("storage", "ok", f"Writable; disk {percent}% full")


def check_worker(db: Session) -> Check:
    if not os.getenv("REDIS_URL", "").strip():
        return Check("worker", "warn", "REDIS_URL isn't set, so no background worker can run")
    return _age_check(
        db, "worker", "The worker", WORKER_STALE_AFTER, "No background worker has reported in", fail_when_stale=True
    )


def check_backups(db: Session) -> Check:
    return _age_check(
        db, "backup", "The last backup", BACKUP_STALE_AFTER, "No backup has been recorded (ops/backup.sh)", False
    )


def check_restore_drill(db: Session) -> Check:
    return _age_check(
        db,
        "restore_drill",
        "The last restore drill",
        RESTORE_DRILL_STALE_AFTER,
        "No restore drill has been recorded (ops/restore-drill.sh)",
        False,
    )


def check_statistics() -> Check:
    from stats_engine import engine_status

    status = engine_status()
    if not status.available:
        return Check("statistics", "warn", status.message or "R isn't available, so analyses can't run")
    return Check("statistics", "ok", f"R {status.r_version}")


def check_publishing() -> Check:
    from publishing_tools import tool_path

    if tool_path("PANDOC_PATH", "pandoc") is None:
        return Check(
            "publishing", "warn", "Pandoc isn't installed: Word export is simplified, LaTeX and PDF unavailable"
        )
    return Check("publishing", "ok", "Pandoc available")


def check_email() -> Check:
    chosen = emailer.backend()
    if chosen == "smtp":
        return Check("email", "ok", f"SMTP through {os.getenv('SMTP_HOST')}")
    if chosen == "console":
        return Check("email", "warn", "Emails are only written to the log (EMAIL_BACKEND=console)")
    return Check("email", "fail", "Email is off, so password resets and verification links can't be delivered")


def check_billing(db: Session) -> Check:
    import billing

    if not entitlements.billing_enabled():
        return Check("billing", "warn", "BILLING_ENABLED is false, so every account is unlimited")
    try:
        chosen = billing.provider()
    except billing.BillingError as exc:
        return Check("billing", "fail", str(exc))
    if db.scalar(select(models.Plan).where(models.Plan.code == entitlements.free_plan_code())) is None:
        return Check("billing", "fail", f"The free plan ({entitlements.free_plan_code()}) doesn't exist")
    return Check("billing", "ok", f"Enforced; payments through {chosen.label}")


def check_error_reporting() -> Check:
    if not os.getenv("SENTRY_DSN", "").strip():
        return Check("error_reporting", "warn", "SENTRY_DSN isn't set, so errors are only in the logs")
    return Check("error_reporting", "ok", "Sentry configured")


def readiness(db: Session) -> list[Check]:
    checks = [check_database()]
    if checks[0].status == "ok":
        checks.append(check_migrations())
    redis_check = check_redis()
    if redis_check is not None:
        checks.append(redis_check)
    checks.append(check_storage())
    return checks


def system_status(db: Session) -> list[Check]:
    return readiness(db) + [
        check_worker(db),
        check_backups(db),
        check_restore_drill(db),
        check_statistics(),
        check_publishing(),
        check_email(),
        check_billing(db),
        check_error_reporting(),
    ]


# --- Costs ---


def spend(db: Session, since, until=None) -> dict[str, Any]:
    """AI use on the server's own API keys (what the operator pays for), by model and by project."""
    conditions = [models.AIRun.key_source == "platform", models.AIRun.created_at >= since]
    if until is not None:
        conditions.append(models.AIRun.created_at < until)
    tokens = func.coalesce(models.AIRun.input_tokens, 0) + func.coalesce(models.AIRun.output_tokens, 0)
    totals = db.execute(
        select(
            func.count(), func.coalesce(func.sum(tokens), 0), func.coalesce(func.sum(models.AIRun.cost_usd), 0)
        ).where(*conditions)
    ).one()
    by_model = db.execute(
        select(
            models.AIRun.provider,
            models.AIRun.model,
            func.count(),
            func.coalesce(func.sum(tokens), 0),
            func.coalesce(func.sum(models.AIRun.cost_usd), 0),
        )
        .where(*conditions)
        .group_by(models.AIRun.provider, models.AIRun.model)
        .order_by(func.coalesce(func.sum(models.AIRun.cost_usd), 0).desc())
    ).all()
    by_project = db.execute(
        select(
            models.Project.id,
            models.Project.title,
            func.count(),
            func.coalesce(func.sum(tokens), 0),
            func.coalesce(func.sum(models.AIRun.cost_usd), 0),
        )
        .join(models.Project, models.Project.id == models.AIRun.project_id)
        .where(*conditions)
        .group_by(models.Project.id, models.Project.title)
        .order_by(func.coalesce(func.sum(models.AIRun.cost_usd), 0).desc())
        .limit(20)
    ).all()
    # A literal (not a bound parameter) so PostgreSQL sees the same expression in SELECT and GROUP BY.
    day = func.date_trunc(literal_column("'day'"), models.AIRun.created_at).label("day")
    by_day = db.execute(
        select(day, func.coalesce(func.sum(tokens), 0), func.coalesce(func.sum(models.AIRun.cost_usd), 0))
        .where(*conditions)
        .group_by(day)
        .order_by(day)
    ).all()
    unpriced = db.scalar(select(func.count()).where(*conditions, models.AIRun.cost_usd.is_(None))) or 0

    def money(value: Decimal | float | int) -> float:
        return round(float(value), 4)

    return {
        "runs": totals[0],
        "tokens": int(totals[1]),
        "cost_usd": money(totals[2]),
        "runs_without_prices": unpriced,
        "by_model": [
            {"provider": p, "model": m, "runs": n, "tokens": int(t), "cost_usd": money(c)} for p, m, n, t, c in by_model
        ],
        "by_project": [
            {"project_id": i, "title": title, "runs": n, "tokens": int(t), "cost_usd": money(c)}
            for i, title, n, t, c in by_project
        ],
        "by_day": [{"day": day.date().isoformat(), "tokens": int(t), "cost_usd": money(c)} for day, t, c in by_day],
    }


def failures(db: Session, since) -> dict[str, int]:
    return {
        "ai_jobs": db.scalar(
            select(func.count()).where(models.AIJob.status == "failed", models.AIJob.created_at >= since)
        )
        or 0,
        "webhook_deliveries": db.scalar(
            select(func.count()).where(
                models.WebhookDelivery.status == "failed", models.WebhookDelivery.created_at >= since
            )
        )
        or 0,
        "billing_events": db.scalar(
            select(func.count()).where(models.BillingEvent.status == "failed", models.BillingEvent.received_at >= since)
        )
        or 0,
    }


def daily_report(db: Session) -> int:
    """Tell platform administrators yesterday's AI spend on the server's keys, failures, and any health problems.
    Returns how many administrators were notified."""
    now = models.utcnow()
    since = now - timedelta(days=1)
    summary = spend(db, since)
    failed = failures(db, since)
    problems = [check for check in system_status(db) if check.status != "ok"]
    lines = [
        f"AI on the server's keys in the last 24 hours: {summary['runs']} runs, {summary['tokens']:,} tokens, "
        f"${summary['cost_usd']:.2f}"
        + (
            f" ({summary['runs_without_prices']} runs without catalog prices)" if summary["runs_without_prices"] else ""
        ),
        f"Failures: {failed['ai_jobs']} AI jobs, {failed['webhook_deliveries']} webhook deliveries, "
        f"{failed['billing_events']} billing events",
    ]
    if problems:
        lines.append("Health: " + "; ".join(f"{check.name} ({check.status}): {check.detail}" for check in problems))
    else:
        lines.append("Health: every check passed")
    admins = db.scalars(
        select(models.User.id).where(models.User.is_platform_admin.is_(True), models.User.is_active.is_(True))
    ).all()
    serious = any(check.status == "fail" for check in problems)
    title = ("Action needed: " if serious else "") + f"Operations report for {since:%d %b %Y}"
    for admin_id in admins:
        notifications.notify(
            db, user_id=admin_id, project_id=None, kind="alert", title=title, body="\n".join(lines), link="/admin"
        )
    db.commit()
    return len(admins)


def reconcile_subscriptions(db: Session) -> int:
    """End administrator-assigned and development subscriptions whose paid period (plus the grace period) has passed.
    Stripe subscriptions end through Stripe's own webhooks."""
    cutoff = models.utcnow() - SUBSCRIPTION_GRACE
    expired = db.scalars(
        select(models.Subscription).where(
            models.Subscription.provider.in_(("manual", "dev")),
            models.Subscription.status.in_(tuple(entitlements.ACTIVE_STATUSES)),
            models.Subscription.current_period_end.is_not(None),
            models.Subscription.current_period_end < cutoff,
        )
    ).all()
    for subscription in expired:
        subscription.status = "canceled"
    db.commit()
    return len(expired)


def email_configured() -> bool:
    return emailer.delivers()
