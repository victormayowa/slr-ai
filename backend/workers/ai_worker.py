"""arq worker that runs background AI jobs. Start one or more next to the API:

    uv run arq workers.ai_worker.WorkerSettings

It needs the same environment as the API (DATABASE_URL, REDIS_URL, DATA_ENCRYPTION_KEY, and provider keys).
"""

import logging
import os
from datetime import timedelta
from typing import Any, ClassVar

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
    functions: ClassVar[list[Any]] = [run_ai_job]
    on_startup = fail_abandoned_jobs
    queue_name = QUEUE_NAME
    redis_settings = redis_settings()
    max_jobs = int(os.getenv("AI_WORKER_MAX_JOBS", "4"))
    job_timeout = JOB_TIMEOUT_SECONDS
    # Jobs aren't retried automatically: records already processed keep their results, so a person reruns the job.
    max_tries = 1
