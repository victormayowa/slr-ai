"""AI work done per record by background jobs: screening, extraction, and appraisal suggestions, and embeddings.

`prepare_task` checks everything a task needs, so the API can refuse a job before queueing it; the worker checks again
when the job runs, because the project may have changed in between. Records are processed in chunks, and every outcome,
including failures, is committed as each chunk finishes so progress is visible while the job runs.
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_access import new_ai_run, project_ai, project_embedding_ai, record_usage
from audit import record_event
from database import SessionLocal
from documents import require_documents_open, retrieve_full_texts
from llm.prompts import APPRAISAL_PROMPT, EXTRACTION_PROMPT, SCREENING_PROMPT, PromptTemplate
from llm.runner import AIContext, AIResult, embed
from permissions import Permission, has_permission
from projects_routes import ProjectAccess
from records_routes import with_record_details
from review_data import final_decision
from services.ai_screening import (
    Eligibility,
    ExtractedField,
    assess_risk_of_bias,
    evaluate_eligibility,
    extract_data_from_paper,
)
from services.errors import LLMError
from workflow import WorkflowError, require_stage_open

logger = logging.getLogger(__name__)

MAX_RECORDS_PER_JOB = 500
CHUNK_SIZE = 10
TASK_PERMISSIONS = {
    "screening": Permission.SCREEN,
    "extraction": Permission.EXTRACT,
    "appraisal": Permission.APPRAISE,
    "embedding": Permission.RUN_SEARCH,
    "fulltext": Permission.EXTRACT,
}
# The workflow stage each record task changes. Embeddings are derived data and don't depend on a stage.
TASK_STAGES = {"screening": "screening", "extraction": "extraction", "appraisal": "appraisal"}
# Recorded as the prompt version of embedding runs; change it whenever paper_text changes.
EMBEDDING_TEXT_VERSION = "embedding-text-v1"


class TaskNotReady(Exception):
    """A task can't run as requested. The message is safe to show users."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def paper_text(record: models.Record) -> str:
    return f"Title: {record.title}\nAbstract: {record.abstract}"


def load_records(db: Session, project_id: int, record_ids: list[int]) -> list[models.Record]:
    """The project's records with these ids, in the order requested."""
    unique_ids = list(dict.fromkeys(record_ids))
    rows = db.scalars(
        with_record_details(
            select(models.Record).where(models.Record.project_id == project_id, models.Record.id.in_(unique_ids))
        )
    ).all()
    by_id = {record.id: record for record in rows}
    if len(by_id) != len(unique_ids):
        raise TaskNotReady("One or more records were not found in this project", status_code=404)
    return [by_id[record_id] for record_id in unique_ids]


def _require_included(records: list[models.Record]) -> None:
    not_included = [record.id for record in records if final_decision(record) != "include"]
    if not_included:
        raise TaskNotReady(f"Only records a reviewer has included can be processed. Not included: {not_included[:20]}")


@dataclass
class PreparedTask:
    ai: AIContext
    prompt: PromptTemplate
    call: Callable[[models.Record], Awaitable[AIResult[Any]]]
    store: Callable[[models.AIRun, Any], None]


def _prepare_screening(db: Session, access: ProjectAccess, records: list[models.Record]) -> PreparedTask:
    if any(record.duplicate_of_id is not None for record in records):
        raise TaskNotReady("Duplicate records are not screened")
    accepted = db.scalars(
        select(models.Criterion)
        .where(models.Criterion.project_id == access.project.id, models.Criterion.status == "accepted")
        .order_by(models.Criterion.id)
    ).all()
    inclusion = [c.text for c in accepted if c.kind == "inclusion"]
    exclusion = [c.text for c in accepted if c.kind == "exclusion"]
    if not inclusion:
        raise TaskNotReady("Accept at least one inclusion criterion before screening")
    criteria = "Include only if all of these apply:\n- " + "\n- ".join(inclusion)
    criteria += "\n\nExclude if any of these apply:\n- " + ("\n- ".join(exclusion) or "(none specified)")
    ai = project_ai(db, access)

    async def call(record: models.Record) -> AIResult[Eligibility]:
        return await evaluate_eligibility(ai, paper_text(record), criteria)

    def store(run: models.AIRun, result: Eligibility) -> None:
        run.screening = models.ScreeningSuggestion(
            decision=result.decision,
            reasoning=result.reasoning,
            supporting_quote=result.supporting_quote,
            quote_verified=result.quote_verified,
        )

    return PreparedTask(ai, SCREENING_PROMPT, call, store)


def _prepare_extraction(db: Session, access: ProjectAccess, records: list[models.Record]) -> PreparedTask:
    fields = list(access.project.extraction_fields)
    if not fields:
        raise TaskNotReady("Add at least one extraction field first")
    _require_included(records)
    field_names = [field.name for field in fields]
    fields_by_name = {field.name: field for field in fields}
    ai = project_ai(db, access)

    async def call(record: models.Record) -> AIResult[list[ExtractedField]]:
        return await extract_data_from_paper(ai, paper_text(record), field_names)

    def store(run: models.AIRun, extracted: list[ExtractedField]) -> None:
        run.extraction_values = [
            models.ExtractionSuggestion(
                field=fields_by_name[item.field],
                value=item.value,
                evidence_quote=item.quote,
                quote_verified=item.quote_verified,
            )
            for item in extracted
        ]

    return PreparedTask(ai, EXTRACTION_PROMPT, call, store)


def _prepare_appraisal(db: Session, access: ProjectAccess, records: list[models.Record]) -> PreparedTask:
    if access.project.protocol is None:
        raise TaskNotReady("This project has no protocol", status_code=404)
    tool = access.project.protocol.rob_tool
    _require_included(records)
    ai = project_ai(db, access)

    async def call(record: models.Record) -> AIResult[dict[str, str]]:
        return await assess_risk_of_bias(ai, paper_text(record), tool)

    def store(run: models.AIRun, judgments: dict[str, str]) -> None:
        run.appraisal = models.AppraisalSuggestion(tool=tool, judgments=judgments)

    return PreparedTask(ai, APPRAISAL_PROMPT, call, store)


_PREPARERS = {"screening": _prepare_screening, "extraction": _prepare_extraction, "appraisal": _prepare_appraisal}


def prepare_task(db: Session, access: ProjectAccess, task: str, records: list[models.Record]) -> PreparedTask:
    preparer = _PREPARERS.get(task)
    if preparer is None:
        raise TaskNotReady(f"Unknown AI task: {task}", status_code=404)
    return preparer(db, access, records)


async def process_records(
    db: Session, access: ProjectAccess, job: models.AIJob, prepared: PreparedTask, records: list[models.Record]
) -> None:
    for start in range(0, len(records), CHUNK_SIZE):
        chunk = records[start : start + CHUNK_SIZE]
        outcomes = await asyncio.gather(*(prepared.call(record) for record in chunk), return_exceptions=True)
        for record, outcome in zip(chunk, outcomes, strict=True):
            run = new_ai_run(access, job.task, prepared.prompt, prepared.ai)
            record.ai_runs.append(run)
            if isinstance(outcome, LLMError):
                run.status, run.error = "failed", str(outcome)
                record_usage(run, prepared.ai, outcome.usage)
                job.failed += 1
            elif isinstance(outcome, Exception):
                logger.error("Unexpected error during AI %s", job.task, exc_info=outcome)
                run.status, run.error = "failed", "Unexpected server error while processing this record"
                job.failed += 1
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                run.status = "succeeded"
                record_usage(run, prepared.ai, outcome.usage)
                prepared.store(run, outcome.value)
        job.processed += len(chunk)
        db.commit()

    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action=f"ai.{job.task}",
        entity_type="ai_job",
        entity_id=job.id,
        details={
            "provider": prepared.ai.provider.id,
            "model": prepared.ai.model,
            "prompt_version": prepared.prompt.id,
            "key_source": prepared.ai.key_source,
            "record_ids": [record.id for record in records],
            "failed": job.failed,
        },
    )
    db.commit()


async def embed_records(db: Session, access: ProjectAccess, job: models.AIJob, records: list[models.Record]) -> None:
    """Embed each record's title and abstract with the project's embedding model, skipping unchanged records."""
    ai = project_embedding_ai(db, access)
    model_ref = f"{ai.provider.id}/{ai.model}"
    existing = {
        row.record_id: row
        for row in db.scalars(
            select(models.RecordEmbedding).where(
                models.RecordEmbedding.model == model_ref,
                models.RecordEmbedding.record_id.in_([record.id for record in records]),
            )
        )
    }
    pending: list[tuple[int, str, str]] = []
    for record in records:
        text = paper_text(record)
        digest = hashlib.sha256(text.encode()).hexdigest()
        row = existing.get(record.id)
        if row is not None and row.content_sha256 == digest:
            job.processed += 1
        else:
            pending.append((record.id, text, digest))
    skipped = job.processed
    db.commit()

    batch_size = ai.provider.embedding_batch_size
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        run = models.AIRun(
            project_id=access.project.id,
            task="embedding",
            provider=ai.provider.id,
            model=ai.model,
            prompt_version=EMBEDDING_TEXT_VERSION,
            key_source=ai.key_source,
            triggered_by_id=access.user.id,
        )
        db.add(run)
        try:
            result = await embed(ai, [text for _, text, _ in batch])
        except LLMError as exc:
            run.status, run.error = "failed", str(exc)
            record_usage(run, ai, exc.usage)
            job.failed += len(batch)
            job.error = job.error or str(exc)
        else:
            run.status = "succeeded"
            record_usage(run, ai, result.usage)
            for (record_id, _, digest), vector in zip(batch, result.value, strict=True):
                row = existing.get(record_id)
                if row is None:
                    row = models.RecordEmbedding(record_id=record_id, project_id=access.project.id, model=model_ref)
                    db.add(row)
                row.content_sha256, row.embedding, row.created_at = digest, vector, models.utcnow()
        job.processed += len(batch)
        db.commit()

    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="ai.embedding",
        entity_type="ai_job",
        entity_id=job.id,
        details={
            "provider": ai.provider.id,
            "model": ai.model,
            "key_source": ai.key_source,
            "embedded": len(pending) - job.failed,
            "unchanged": skipped,
            "failed": job.failed,
        },
    )
    db.commit()


def _job_access(db: Session, job: models.AIJob) -> ProjectAccess:
    """The job starter's access, checked again now: they may have left the project or changed role since."""
    membership = None
    if job.created_by_id is not None:
        membership = db.scalar(
            select(models.ProjectMember).where(
                models.ProjectMember.project_id == job.project_id, models.ProjectMember.user_id == job.created_by_id
            )
        )
    if membership is None or not has_permission(membership.role, TASK_PERMISSIONS[job.task]):
        raise TaskNotReady("The person who started this job no longer has permission to run it")
    return ProjectAccess(user=membership.user, project=membership.project, membership=membership)


async def run_job(job_id: int) -> str:
    """Run a queued job to the end and return its final status: "completed" or "failed".

    A completed job can still have failed records; their errors are stored on each record's AI run.
    """
    with SessionLocal() as db:
        job = db.get(models.AIJob, job_id)
        if job is None:
            return "missing"
        if job.status not in ("queued", "running"):
            return job.status
        job.status, job.started_at = "running", models.utcnow()
        job.processed, job.failed, job.error = 0, 0, None
        db.commit()
        try:
            access = _job_access(db, job)
            if job.task == "embedding":
                # Records deleted or marked as duplicates since the job was queued are left out.
                records = db.scalars(
                    select(models.Record)
                    .where(
                        models.Record.project_id == job.project_id,
                        models.Record.id.in_(job.record_ids),
                        models.Record.duplicate_of_id.is_(None),
                    )
                    .order_by(models.Record.id)
                ).all()
                job.total = len(records)
                await embed_records(db, access, job, list(records))
            elif job.task == "fulltext":
                require_documents_open(db, job.project_id)
                records = [
                    record
                    for record in load_records(db, job.project_id, job.record_ids)
                    if record.duplicate_of_id is None
                ]
                job.total = len(records)
                await retrieve_full_texts(db, access, job, records)
            else:
                require_stage_open(db, job.project_id, TASK_STAGES[job.task])
                records = load_records(db, job.project_id, job.record_ids)
                await process_records(db, access, job, prepare_task(db, access, job.task, records), records)
            job.status = "completed"
        except (TaskNotReady, WorkflowError) as exc:
            db.rollback()
            job.status, job.error = "failed", str(exc)
        except HTTPException as exc:
            db.rollback()
            job.status, job.error = "failed", str(exc.detail)
        except Exception:
            logger.exception("AI job %s failed", job_id)
            db.rollback()
            job.status, job.error = "failed", "Unexpected server error while running this job"
        job.finished_at = models.utcnow()
        db.commit()
        return job.status
