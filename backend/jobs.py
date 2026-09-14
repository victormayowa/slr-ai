"""Background AI jobs. The API creates a job and queues it in Redis; a worker (workers/ai_worker.py) runs it.

There is no in-process fallback: without Redis, starting a job fails with a clear message instead of holding an API
request open for minutes.
"""

import logging
import os
from datetime import timedelta

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from audit import record_event
from projects_routes import ProjectAccess

logger = logging.getLogger(__name__)

QUEUE_NAME = "omnireview:ai-jobs"
JOB_TIMEOUT_SECONDS = int(os.getenv("AI_JOB_TIMEOUT_SECONDS", "3600"))
ACTIVE_STATUSES = ("queued", "running")


class JobQueueUnavailable(Exception):
    """Jobs can't be queued. The message is safe to show users."""


def redis_settings() -> RedisSettings:
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        raise JobQueueUnavailable("Background AI jobs need Redis: set REDIS_URL and start a worker (see README.md).")
    return RedisSettings.from_dsn(url)


def job_out(job: models.AIJob) -> dict:
    return {
        "id": job.id,
        "task": job.task,
        "status": job.status,
        "total": job.total,
        "processed": job.processed,
        "failed": job.failed,
        "error": job.error,
        "created_by": job.created_by.full_name if job.created_by else None,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


async def enqueue_job(job_id: int) -> None:
    pool = await create_pool(redis_settings())
    try:
        await pool.enqueue_job("run_ai_job", job_id, _job_id=f"ai-job-{job_id}", _queue_name=QUEUE_NAME)
    finally:
        await pool.aclose()


async def start_job(db: Session, access: ProjectAccess, task: str, record_ids: list[int]) -> models.AIJob:
    """Create and queue a job. A project runs one job of each task at a time."""
    # Lock the project row so two requests can't both start the same task.
    db.execute(select(models.Project.id).where(models.Project.id == access.project.id).with_for_update())
    # A job older than twice the timeout was abandoned (for example its worker was killed) and doesn't block new ones.
    recent = models.utcnow() - timedelta(seconds=JOB_TIMEOUT_SECONDS * 2)
    active = db.scalar(
        select(models.AIJob.id)
        .where(
            models.AIJob.project_id == access.project.id,
            models.AIJob.task == task,
            models.AIJob.status.in_(ACTIVE_STATUSES),
            models.AIJob.created_at > recent,
        )
        .limit(1)
    )
    if active is not None:
        raise HTTPException(
            status_code=409, detail=f"An AI {task} job is already running in this project. Wait for it to finish."
        )

    job = models.AIJob(
        project_id=access.project.id,
        task=task,
        status="queued",
        record_ids=record_ids,
        total=len(record_ids),
        processed=0,
        failed=0,
        created_by_id=access.user.id,
    )
    db.add(job)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="ai_job.queued",
        entity_type="ai_job",
        entity_id=job.id,
        details={"task": task, "records": len(record_ids)},
    )
    db.commit()

    try:
        await enqueue_job(job.id)
    except Exception as exc:
        if isinstance(exc, JobQueueUnavailable):
            message = str(exc)
        else:
            logger.exception("Could not queue AI job %s", job.id)
            message = "The job queue is unavailable. Try again shortly."
        job.status, job.error, job.finished_at = "failed", message, models.utcnow()
        db.commit()
        raise HTTPException(status_code=503, detail=message) from exc
    db.refresh(job)
    return job
