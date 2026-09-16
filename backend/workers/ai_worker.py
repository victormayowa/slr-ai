"""arq worker that runs background AI jobs. Start one or more next to the API:

    uv run arq workers.ai_worker.WorkerSettings

It needs the same environment as the API (DATABASE_URL, REDIS_URL, DATA_ENCRYPTION_KEY, and provider keys).
"""

import logging
import os
from datetime import timedelta
from typing import Any, ClassVar

from arq import cron
from sqlalchemy import select

import models
from ai_tasks import run_job
from database import SessionLocal
from jobs import JOB_TIMEOUT_SECONDS, QUEUE_NAME, redis_settings
from observability import configure_logging, configure_sentry

configure_logging()
configure_sentry()
logger = logging.getLogger(__name__)


async def run_ai_job(ctx: dict[str, Any], job_id: int) -> str:
    return await run_job(job_id)


async def run_benchmark_job(ctx: dict[str, Any], run_id: int) -> None:
    """Run one benchmark data set against a model (or the R engine); see benchmarks.py."""
    from benchmarks import run_benchmark

    with SessionLocal() as db:
        await run_benchmark(db, run_id)


async def deliver_webhooks(ctx: dict[str, Any]) -> None:
    """Every minute: POST due webhook deliveries, retrying failures with increasing delays."""
    from webhooks import deliver_due

    with SessionLocal() as db:
        delivered = deliver_due(db)
    if delivered:
        logger.info("Delivered %s webhook(s)", delivered)


async def send_notification_emails(ctx: dict[str, Any]) -> None:
    """Every 15 minutes: email unread notifications to people who allow email (when SMTP is configured)."""
    from notifications import deliver_pending_emails

    with SessionLocal() as db:
        sent = deliver_pending_emails(db)
    if sent:
        logger.info("Emailed %s notification(s)", sent)


async def remind_task_deadlines(ctx: dict[str, Any]) -> None:
    """Daily: notify assignees of tasks that are due tomorrow or overdue."""
    from notifications import remind_due_tasks

    with SessionLocal() as db:
        reminded = remind_due_tasks(db)
    if reminded:
        logger.info("Sent %s task reminder(s)", reminded)


async def run_due_surveillance(ctx: dict[str, Any]) -> None:
    """Hourly: rerun due surveillance searches, and check watched feeds (daily) and retractions (weekly)."""
    from surveillance import run_due

    with SessionLocal() as db:
        runs = run_due(db)
        db.commit()
    if runs:
        logger.info("Ran %s surveillance checks", len(runs))


async def fail_abandoned_jobs(ctx: dict[str, Any]) -> None:
    """Mark jobs still running long after the timeout as failed, for example because their worker was killed."""
    cutoff = models.utcnow() - timedelta(seconds=JOB_TIMEOUT_SECONDS * 2)
    with SessionLocal() as db:
        abandoned = db.scalars(
            select(models.AIJob).where(models.AIJob.status == "running", models.AIJob.started_at < cutoff)
        ).all()
        for job in abandoned:
            job.status, job.error, job.finished_at = (
                "failed",
                "The job stopped unexpectedly. Start it again.",
                models.utcnow(),
            )
        db.commit()
    if abandoned:
        logger.warning("Marked %s abandoned AI job(s) as failed", len(abandoned))


class WorkerSettings:
    functions: ClassVar[list[Any]] = [run_ai_job, run_benchmark_job]
    cron_jobs: ClassVar[list[Any]] = [
        cron(run_due_surveillance, minute={5}),
        cron(deliver_webhooks, second={0}),
        cron(send_notification_emails, minute={0, 15, 30, 45}),
        cron(remind_task_deadlines, hour={7}, minute={0}),
    ]
    on_startup = fail_abandoned_jobs
    queue_name = QUEUE_NAME
    redis_settings = redis_settings()
    max_jobs = int(os.getenv("AI_WORKER_MAX_JOBS", "4"))
    job_timeout = JOB_TIMEOUT_SECONDS
    # Jobs aren't retried automatically: records already processed keep their results, so a person reruns the job.
    max_tries = 1
