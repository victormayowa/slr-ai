"""Starting background AI jobs (record and study batches, and embeddings) and checking their progress."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_access import project_embedding_ai
from ai_tasks import (
    MAX_RECORDS_PER_JOB,
    TASK_STAGES,
    TaskNotReady,
    load_records,
    load_studies,
    prepare_extraction,
    prepare_task,
)
from database import get_db
from jobs import job_out, start_job
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from workflow import require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["jobs"])

MAX_RECORDS_PER_EMBEDDING_JOB = 5000


class AIBatchRequest(BaseModel):
    record_ids: list[int] = Field(min_length=1, max_length=MAX_RECORDS_PER_JOB)


class StudyBatchRequest(BaseModel):
    study_ids: list[int] = Field(min_length=1, max_length=MAX_RECORDS_PER_JOB)


async def _start_record_job(db: Session, access: ProjectAccess, task: str, record_ids: list[int]) -> dict:
    require_stage_open(db, access.project.id, TASK_STAGES[task])
    records = load_records(db, access.project.id, record_ids)
    # Refuse now anything the worker would refuse, such as a missing API key or records that aren't included.
    prepare_task(db, access, task, records)
    job = await start_job(db, access, task, [record.id for record in records])
    return job_out(job)


@router.post("/screening/ai", status_code=202, dependencies=[Depends(ai_rate_limit)])
async def start_screening_job(
    body: AIBatchRequest,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    """Queue AI title and abstract screening suggestions for the records. Poll GET /jobs/{job_id} for progress."""
    return await _start_record_job(db, access, "screening", body.record_ids)


@router.post("/full-text-screening/ai", status_code=202, dependencies=[Depends(ai_rate_limit)])
async def start_full_text_screening_job(
    body: AIBatchRequest,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    """Queue AI full-text screening suggestions, read from each record's parsed full text."""
    return await _start_record_job(db, access, "fulltext_screening", body.record_ids)


@router.post("/extraction/ai", status_code=202, dependencies=[Depends(ai_rate_limit)])
async def start_extraction_job(
    body: StudyBatchRequest,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Queue AI extraction suggestions for the studies, read from their reports' full texts."""
    require_stage_open(db, access.project.id, "extraction")
    studies = load_studies(db, access.project.id, body.study_ids)
    prepare_extraction(db, access, studies)
    job = await start_job(db, access, "extraction", [study.id for study in studies])
    return job_out(job)


@router.post("/appraisal/ai", status_code=202, dependencies=[Depends(ai_rate_limit)])
async def start_appraisal_job(
    body: AIBatchRequest,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    return await _start_record_job(db, access, "appraisal", body.record_ids)


@router.post("/embeddings", status_code=202, dependencies=[Depends(ai_rate_limit)])
async def start_embedding_job(
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)), db: Session = Depends(get_db)
):
    """Queue embeddings of every unique record, used to find records that are similar in meaning."""
    record_ids = list(
        db.scalars(
            select(models.Record.id)
            .where(models.Record.project_id == access.project.id, models.Record.duplicate_of_id.is_(None))
            .order_by(models.Record.id)
            .limit(MAX_RECORDS_PER_EMBEDDING_JOB)
        )
    )
    if not record_ids:
        raise TaskNotReady("Search for or import records first")
    project_embedding_ai(db, access)
    job = await start_job(db, access, "embedding", record_ids)
    return job_out(job)


@router.get("/jobs")
def list_jobs(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)):
    jobs = db.scalars(
        select(models.AIJob)
        .where(models.AIJob.project_id == access.project.id)
        .order_by(models.AIJob.id.desc())
        .limit(20)
    )
    return [job_out(job) for job in jobs]


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    return job_out(get_in_project(db, models.AIJob, job_id, access.project.id, "Job"))
