"""AI work done by background jobs: screening suggestions (title and abstract, and full text), extraction suggestions
per study, appraisal suggestions, embeddings, and full-text retrieval.

`prepare_task` and `prepare_extraction` check everything a task needs, so the API can refuse a job before queueing it;
the worker checks again when the job runs, because the project may have changed in between. Work is processed in
chunks, and every outcome, including failures, is committed as each chunk finishes so progress is visible while the job
runs.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import models
from ai_access import new_ai_run, project_ai, project_embedding_ai, record_usage
from appraisal_tools import ANSWER_SETS, JUDGMENT_SETS, TOOLS
from audit import record_event
from database import SessionLocal
from documents import best_full_texts, document_passages, require_documents_open, retrieve_full_texts
from extraction_data import included_records, project_fields
from extraction_values import parse_suggestion
from llm.grounding import Passage, locate_quote, quote_is_grounded, select_passages
from llm.prompts import APPRAISAL_PROMPT, EXTRACTION_PROMPT, REPORTING_PROMPT, SCREENING_PROMPT, PromptTemplate
from llm.runner import AIContext, AIResult, embed
from permissions import Permission, has_permission
from projects_routes import ProjectAccess
from records_routes import with_record_details
from reporting_checklists import CHECKLISTS, STATUSES
from review_data import FULL_TEXT, TITLE_ABSTRACT
from review_settings import review_policy
from services.ai_appraisal import AppraisalOutput, ReportingOutput, suggest_appraisal, suggest_reporting
from services.ai_screening import (
    CriterionPrompt,
    Eligibility,
    FieldPrompt,
    StudyExtractionOutput,
    evaluate_eligibility,
    extract_study_data,
)
from services.errors import LLMError
from statistics_jobs import process_analysis_runs
from workflow import WorkflowError, require_stage_open

logger = logging.getLogger(__name__)

MAX_RECORDS_PER_JOB = 500
CHUNK_SIZE = 10
# Characters of full text sent to the AI for one report or study (about 30,000 tokens).
FULL_TEXT_MAX_CHARS = 120_000
TASK_PERMISSIONS = {
    "screening": Permission.SCREEN,
    "fulltext_screening": Permission.SCREEN,
    "extraction": Permission.EXTRACT,
    "appraisal": Permission.APPRAISE,
    "reporting": Permission.APPRAISE,
    "statistics": Permission.RUN_ANALYSIS,
    "embedding": Permission.RUN_SEARCH,
    "fulltext": Permission.EXTRACT,
}
# The workflow stage each AI task works in. Embeddings are derived data and don't depend on a stage.
TASK_STAGES = {
    "screening": "screening",
    "fulltext_screening": "full_text_screening",
    "extraction": "extraction",
    "appraisal": "appraisal",
    "reporting": "appraisal",
    "statistics": "synthesis",
}
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


def load_studies(db: Session, project_id: int, study_ids: list[int]) -> list[models.Study]:
    unique_ids = list(dict.fromkeys(study_ids))
    rows = db.scalars(
        select(models.Study)
        .where(models.Study.project_id == project_id, models.Study.id.in_(unique_ids))
        .options(
            selectinload(models.Study.reports).selectinload(models.StudyReport.record), selectinload(models.Study.arms)
        )
    ).all()
    by_id = {study.id: study for study in rows}
    if len(by_id) != len(unique_ids):
        raise TaskNotReady("One or more studies were not found in this project", status_code=404)
    return [by_id[study_id] for study_id in unique_ids]


def _require_included(db: Session, project_id: int, records: list[models.Record]) -> None:
    policy = review_policy(db, project_id)
    not_included = [record.id for record in records if not policy.included(record)]
    if not_included:
        raise TaskNotReady(
            f"Only records included at full-text screening can be processed. Not included: {not_included[:20]}"
        )


@dataclass
class PreparedTask:
    ai: AIContext
    prompt: PromptTemplate
    call: Callable[[models.Record], Awaitable[AIResult[Any]]]
    store: Callable[[models.AIRun, Any], None]


def _accepted_criteria(db: Session, project_id: int) -> list[CriterionPrompt]:
    accepted = db.scalars(
        select(models.Criterion)
        .where(models.Criterion.project_id == project_id, models.Criterion.status == "accepted")
        .order_by(models.Criterion.id)
    ).all()
    if not any(c.kind == "inclusion" for c in accepted):
        raise TaskNotReady("Accept at least one inclusion criterion before screening")
    return [CriterionPrompt(c.id, c.kind, c.text) for c in accepted]


def _screening_store(stage: str, documents: dict[int, models.Document]) -> Callable[[models.AIRun, Any], None]:
    def store(run: models.AIRun, result: Eligibility) -> None:
        document = documents.get(run.record.id) if run.record is not None else None
        run.screening = models.ScreeningSuggestion(
            stage=stage,
            decision=result.decision,
            reasoning=result.reasoning,
            supporting_quote=result.supporting_quote,
            quote_verified=result.quote_verified,
            confidence=result.confidence,
            criteria_judgments=result.criteria_judgments,
            document_id=document.id if document else None,
            supporting_span_id=result.supporting_span_id,
        )

    return store


def _prepare_screening(db: Session, access: ProjectAccess, records: list[models.Record]) -> PreparedTask:
    if any(record.duplicate_of_id is not None for record in records):
        raise TaskNotReady("Duplicate records are not screened")
    criteria = _accepted_criteria(db, access.project.id)
    ai = project_ai(db, access)

    async def call(record: models.Record) -> AIResult[Eligibility]:
        return await evaluate_eligibility(ai, paper_text(record), criteria)

    return PreparedTask(ai, SCREENING_PROMPT, call, _screening_store(TITLE_ABSTRACT, {}))


def _prepare_full_text_screening(db: Session, access: ProjectAccess, records: list[models.Record]) -> PreparedTask:
    if any(record.duplicate_of_id is not None for record in records):
        raise TaskNotReady("Duplicate records are not screened")
    policy = review_policy(db, access.project.id)
    not_sought = [record.id for record in records if not policy.sought(record)]
    if not_sought:
        raise TaskNotReady(f"Only records included at title and abstract are screened at full text: {not_sought[:20]}")
    criteria = _accepted_criteria(db, access.project.id)
    documents = best_full_texts(db, [record.id for record in records])
    missing = [record.id for record in records if record.id not in documents]
    if missing:
        raise TaskNotReady(f"These records have no readable full text yet (see Full Texts): {missing[:20]}")
    passages = document_passages(db, [document.id for document in documents.values()])
    ai = project_ai(db, access)

    async def call(record: models.Record) -> AIResult[Eligibility]:
        selected = select_passages(passages[documents[record.id].id], FULL_TEXT_MAX_CHARS)
        return await evaluate_eligibility(ai, "", criteria, selected)

    return PreparedTask(ai, SCREENING_PROMPT, call, _screening_store(FULL_TEXT, documents))


_PREPARERS = {
    "screening": _prepare_screening,
    "fulltext_screening": _prepare_full_text_screening,
}


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


# --- Extraction, per study ---


@dataclass
class StudyContext:
    passages: list[Passage]
    abstract_text: str
    primary_record_id: int


@dataclass
class PreparedExtraction:
    ai: AIContext
    fields: list[models.ExtractionField]
    contexts: dict[int, StudyContext]


def prepare_extraction(db: Session, access: ProjectAccess, studies: list[models.Study]) -> PreparedExtraction:
    fields = project_fields(db, access.project.id)
    if not fields:
        raise TaskNotReady("Add at least one extraction field first")
    included = {record.id for record in included_records(db, access.project.id, review_policy(db, access.project.id))}
    not_included = [study.id for study in studies if not any(r.record_id in included for r in study.reports)]
    if not_included:
        raise TaskNotReady(f"Only studies included in the review can be extracted: {not_included[:20]}")
    ai = project_ai(db, access)
    return PreparedExtraction(ai, fields, build_study_contexts(db, studies))


def store_extraction(
    run: models.AIRun,
    study: models.Study,
    context: StudyContext,
    fields: list[models.ExtractionField],
    output: StudyExtractionOutput,
) -> None:
    """Keep each suggested value with its grounding: a value whose quote can't be found in the study's text is kept but
    marked ungrounded, and can't be accepted."""
    fields_by_id = {item.id: item for item in fields}
    seen: set[tuple[int, str]] = set()
    for item in output.values:
        field = fields_by_id.get(item.field_id)
        if field is None:
            continue
        arm = (item.arm or "").strip() if field.per_arm else ""
        if (field.id, arm.casefold()) in seen:
            continue
        seen.add((field.id, arm.casefold()))
        quote = (item.quote or "").strip() or None
        span_ids: list[int] = []
        structured = None
        if item.not_reported:
            text, grounding, verified = "Not Reported", "not_reported", None
        else:
            if item.value is not None:
                text = str(item.value)
            elif item.components:
                text = json.dumps(item.components)
            else:
                text = ""
            structured = parse_suggestion(field, text, item.components)
            if quote is None:
                verified = False
            elif context.passages:
                span_id = locate_quote(quote, context.passages, item.passage_ids)
                verified = span_id is not None
                span_ids = [span_id] if span_id is not None else []
            else:
                verified = quote_is_grounded(quote, context.abstract_text)
            grounding = "grounded" if verified else "ungrounded"
        run.extraction_values.append(
            models.ExtractionSuggestion(
                field_id=field.id,
                study_id=study.id,
                arm_label=arm[:200],
                value=text[:20_000],
                structured=structured,
                unit=(item.unit or "")[:40],
                not_reported=item.not_reported,
                evidence_quote=quote,
                quote_verified=verified,
                span_ids=span_ids,
                confidence=item.confidence,
                ambiguous=item.ambiguous,
                grounding=grounding,
            )
        )


async def process_studies(
    db: Session, access: ProjectAccess, job: models.AIJob, prepared: PreparedExtraction, studies: list[models.Study]
) -> None:
    prompts = [
        FieldPrompt(f.id, f.name, f.field_type, f.per_arm, f.unit, tuple(f.options), f.help_text)
        for f in prepared.fields
    ]
    for start in range(0, len(studies), CHUNK_SIZE):
        chunk = studies[start : start + CHUNK_SIZE]
        outcomes = await asyncio.gather(
            *(
                extract_study_data(
                    prepared.ai,
                    prepared.contexts[study.id].passages,
                    prepared.contexts[study.id].abstract_text,
                    prompts,
                    [arm.label for arm in study.arms],
                )
                for study in chunk
            ),
            return_exceptions=True,
        )
        for study, outcome in zip(chunk, outcomes, strict=True):
            context = prepared.contexts[study.id]
            run = new_ai_run(access, "extraction", EXTRACTION_PROMPT, prepared.ai)
            run.study_id, run.record_id = study.id, context.primary_record_id
            db.add(run)
            if isinstance(outcome, LLMError):
                run.status, run.error = "failed", str(outcome)
                record_usage(run, prepared.ai, outcome.usage)
                job.failed += 1
            elif isinstance(outcome, Exception):
                logger.error("Unexpected error during AI extraction", exc_info=outcome)
                run.status, run.error = "failed", "Unexpected server error while processing this study"
                job.failed += 1
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                run.status = "succeeded"
                record_usage(run, prepared.ai, outcome.usage)
                store_extraction(run, study, context, prepared.fields, outcome.value)
        job.processed += len(chunk)
        db.commit()

    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="ai.extraction",
        entity_type="ai_job",
        entity_id=job.id,
        details={
            "provider": prepared.ai.provider.id,
            "model": prepared.ai.model,
            "prompt_version": EXTRACTION_PROMPT.id,
            "key_source": prepared.ai.key_source,
            "study_ids": [study.id for study in studies],
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

    A completed job can still have failed items; their errors are stored on each item's AI run.
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
            elif job.task in ("appraisal", "reporting"):
                require_stage_open(db, job.project_id, "appraisal")
                # For these jobs, record_ids holds assessment ids.
                await process_assessments(db, access, job, job.task, job.record_ids)
            elif job.task == "statistics":
                require_stage_open(db, job.project_id, "synthesis")
                # For statistics jobs, record_ids holds analysis run ids.
                await process_analysis_runs(db, access, job, job.record_ids)
            elif job.task == "extraction":
                require_stage_open(db, job.project_id, "extraction")
                # For extraction jobs, record_ids holds study ids.
                studies = load_studies(db, job.project_id, job.record_ids)
                await process_studies(db, access, job, prepare_extraction(db, access, studies), studies)
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


def build_study_contexts(db: Session, studies: list[models.Study]) -> dict[int, StudyContext]:
    """Each study's full-text passages (primary report first, within the size limit) and its reports' abstracts."""
    record_ids = [report.record_id for study in studies for report in study.reports]
    documents = best_full_texts(db, record_ids)
    passages = document_passages(db, [document.id for document in documents.values()])
    contexts = {}
    for study in studies:
        reports = sorted(study.reports, key=lambda report: (not report.is_primary, report.id))
        study_passages = [
            passage
            for report in reports
            if report.record_id in documents
            for passage in passages[documents[report.record_id].id]
        ]
        contexts[study.id] = StudyContext(
            select_passages(study_passages, FULL_TEXT_MAX_CHARS),
            "\n\n".join(paper_text(report.record) for report in reports),
            reports[0].record_id,
        )
    return contexts


# --- Appraisal and reporting checklists, per assessment ---


def _evidence(context: StudyContext, quote: str | None, passage_id: int | None) -> tuple[bool, list[int]]:
    if not quote:
        return False, []
    if context.passages:
        span_id = locate_quote(quote, context.passages, [passage_id] if passage_id is not None else [])
        return span_id is not None, [span_id] if span_id is not None else []
    return quote_is_grounded(quote, context.abstract_text), []


def store_appraisal(
    db: Session,
    run: models.AIRun,
    assessment: models.AppraisalAssessment,
    context: StudyContext,
    output: AppraisalOutput,
) -> None:
    """Keep AI answers that use the tool's own answer options, with whether their quotes were found in the text."""
    tool = TOOLS[assessment.tool]
    for item in output.answers:
        question = tool.question(item.question_id)
        if question is None or item.answer not in dict(ANSWER_SETS[question.answers]):
            continue
        quote = (item.quote or "").strip() or None
        grounded, spans = _evidence(context, quote, item.passage_id)
        db.add(
            models.AppraisalAISuggestion(
                ai_run_id=run.id,
                assessment_id=assessment.id,
                question_id=item.question_id,
                answer=item.answer,
                rationale=item.rationale.strip(),
                quote=quote,
                span_ids=spans,
                grounded=grounded,
            )
        )
    for judged in output.domains:
        domain = tool.domain(judged.domain)
        if domain is None or judged.judgment not in dict(JUDGMENT_SETS[domain.judgments]):
            continue
        db.add(
            models.AppraisalAISuggestion(
                ai_run_id=run.id,
                assessment_id=assessment.id,
                domain=judged.domain,
                answer=judged.judgment,
                rationale=judged.rationale.strip(),
            )
        )


def store_reporting(
    db: Session,
    run: models.AIRun,
    assessment: models.ReportingAssessment,
    context: StudyContext,
    output: ReportingOutput,
) -> None:
    valid = {item_id for item_id, _, _ in CHECKLISTS[assessment.checklist].items}
    rows = {row.item_id: row for row in assessment.items}
    for item in output.items:
        if item.item_id not in valid or item.status not in STATUSES:
            continue
        row = rows.get(item.item_id)
        if row is None:
            row = models.ReportingItem(item_id=item.item_id)
            assessment.items.append(row)
            rows[item.item_id] = row
        quote = (item.quote or "").strip() or None
        grounded, spans = _evidence(context, quote, item.passage_id)
        row.ai_status, row.ai_rationale, row.ai_quote = item.status, item.rationale.strip(), quote
        row.ai_grounded, row.ai_run_id = (grounded if quote else None), run.id
        if not row.status and spans:
            row.span_ids = spans


async def process_assessments(db: Session, access: ProjectAccess, job: models.AIJob, kind: str, ids: list[int]) -> None:
    """Background job: AI suggestions for appraisal tool questions or reporting checklist items, per assessment."""
    assessments: list[models.AppraisalAssessment | models.ReportingAssessment]
    if kind == "appraisal":
        appraisal = models.AppraisalAssessment
        query = select(appraisal).where(appraisal.project_id == access.project.id, appraisal.id.in_(ids))
        assessments = list(db.scalars(query.order_by(appraisal.id)))
    else:
        reporting = models.ReportingAssessment
        reporting_query = select(reporting).where(reporting.project_id == access.project.id, reporting.id.in_(ids))
        assessments = list(db.scalars(reporting_query.order_by(reporting.id)))
    job.total = len(assessments)
    db.commit()
    ai = project_ai(db, access)
    studies = load_studies(db, access.project.id, sorted({a.study_id for a in assessments}))
    contexts = build_study_contexts(db, studies)
    prompt = APPRAISAL_PROMPT if kind == "appraisal" else REPORTING_PROMPT
    for start in range(0, len(assessments), CHUNK_SIZE):
        chunk = assessments[start : start + CHUNK_SIZE]
        calls: list[Awaitable[AIResult[Any]]] = []
        for a in chunk:
            context = contexts[a.study_id]
            if isinstance(a, models.AppraisalAssessment):
                calls.append(suggest_appraisal(ai, TOOLS[a.tool], a.outcome, context.passages, context.abstract_text))
            else:
                calls.append(suggest_reporting(ai, CHECKLISTS[a.checklist], context.passages, context.abstract_text))
        outcomes = await asyncio.gather(*calls, return_exceptions=True)
        for assessment, outcome in zip(chunk, outcomes, strict=True):
            context = contexts[assessment.study_id]
            run = new_ai_run(access, kind, prompt, ai)
            run.study_id, run.record_id = assessment.study_id, context.primary_record_id
            db.add(run)
            if isinstance(outcome, LLMError):
                run.status, run.error = "failed", str(outcome)
                record_usage(run, ai, outcome.usage)
                job.failed += 1
            elif isinstance(outcome, Exception):
                logger.error("Unexpected error during AI %s", kind, exc_info=outcome)
                run.status, run.error = "failed", "Unexpected server error while processing this assessment"
                job.failed += 1
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                run.status = "succeeded"
                record_usage(run, ai, outcome.usage)
                db.flush()
                if isinstance(assessment, models.AppraisalAssessment) and isinstance(outcome.value, AppraisalOutput):
                    store_appraisal(db, run, assessment, context, outcome.value)
                elif isinstance(assessment, models.ReportingAssessment) and isinstance(outcome.value, ReportingOutput):
                    store_reporting(db, run, assessment, context, outcome.value)
        job.processed += len(chunk)
        db.commit()

    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action=f"ai.{kind}",
        entity_type="ai_job",
        entity_id=job.id,
        details={
            "provider": ai.provider.id,
            "model": ai.model,
            "prompt_version": prompt.id,
            "key_source": ai.key_source,
            "assessment_ids": [a.id for a in assessments],
            "failed": job.failed,
        },
    )
    db.commit()
