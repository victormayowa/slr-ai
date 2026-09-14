"""AI suggestions (screening, extraction, appraisal, synthesis) and reviewers' screening decisions.

AI output is stored as suggestions with provenance; only a reviewer's decision includes or excludes a record,
and extraction, appraisal, and synthesis run only on records a reviewer included.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from audit import record_event
from database import get_db
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from records_routes import record_out, with_record_details
from review_data import TITLE_ABSTRACT, final_decision
from services.ai_screening import (
    APPRAISAL_PROMPT_VERSION,
    EXTRACTION_PROMPT_VERSION,
    SCREENING_PROMPT_VERSION,
    SYNTHESIS_PROMPT_VERSION,
    assess_risk_of_bias,
    evaluate_eligibility,
    extract_data_from_paper,
    generate_meta_analysis,
)
from services.errors import LLMError
from services.llm import Provider, model_for
from workflow import require_stage_open

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["screening"])

MAX_RECORDS_PER_AI_BATCH = 50


class AIBatchRequest(BaseModel):
    record_ids: list[int] = Field(min_length=1, max_length=MAX_RECORDS_PER_AI_BATCH)
    provider: Provider = "gemini"


class DecisionRequest(BaseModel):
    decision: Literal["include", "exclude", "undecided"]


class SynthesisRequest(BaseModel):
    provider: Provider = "gemini"


def _paper_text(record: models.Record) -> str:
    return f"Title: {record.title}\nAbstract: {record.abstract}"


def _load_records(db: Session, project_id: int, record_ids: list[int]) -> list[models.Record]:
    records = db.scalars(
        with_record_details(
            select(models.Record)
            .where(models.Record.project_id == project_id, models.Record.id.in_(record_ids))
            .order_by(models.Record.id)
        )
    ).all()
    if len(records) != len(set(record_ids)):
        raise HTTPException(status_code=404, detail="One or more records were not found in this project")
    return list(records)


def _require_included(records: list[models.Record]) -> None:
    not_included = [record.id for record in records if final_decision(record) != "include"]
    if not_included:
        raise HTTPException(
            status_code=400,
            detail=f"Only records a reviewer has included can be processed. Not included: {not_included[:20]}",
        )


async def _run_per_record(
    db: Session,
    access: ProjectAccess,
    records: list[models.Record],
    task: str,
    provider: str,
    prompt_version: str,
    call: Callable[[models.Record], Awaitable[dict[str, Any]]],
    store: Callable[[models.AIRun, dict[str, Any]], None],
) -> list[dict]:
    """Run an AI task per record, storing each outcome, including failures, as an AIRun."""
    outcomes = await asyncio.gather(*(call(record) for record in records), return_exceptions=True)
    failed = 0
    for record, outcome in zip(records, outcomes, strict=True):
        run = models.AIRun(
            project_id=access.project.id,
            task=task,
            provider=provider,
            model=model_for(provider),
            prompt_version=prompt_version,
            triggered_by_id=access.user.id,
        )
        record.ai_runs.append(run)
        if isinstance(outcome, LLMError):
            run.status, run.error = "failed", str(outcome)
            failed += 1
        elif isinstance(outcome, Exception):
            logger.error("Unexpected error during AI %s", task, exc_info=outcome)
            run.status, run.error = "failed", "Unexpected server error while processing this record"
            failed += 1
        elif isinstance(outcome, BaseException):
            raise outcome
        else:
            run.status = "succeeded"
            store(run, outcome)
    db.flush()

    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action=f"ai.{task}",
        entity_type="record",
        details={
            "provider": provider,
            "model": model_for(provider),
            "prompt_version": prompt_version,
            "record_ids": [record.id for record in records],
            "failed": failed,
        },
    )
    db.commit()
    return [record_out(record, access.user.id) for record in records]


@router.post("/screening/ai", dependencies=[Depends(ai_rate_limit)])
async def suggest_screening_decisions(
    body: AIBatchRequest,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "screening")
    accepted = db.scalars(
        select(models.Criterion)
        .where(models.Criterion.project_id == access.project.id, models.Criterion.status == "accepted")
        .order_by(models.Criterion.id)
    ).all()
    inclusion = [c.text for c in accepted if c.kind == "inclusion"]
    exclusion = [c.text for c in accepted if c.kind == "exclusion"]
    if not inclusion:
        raise HTTPException(status_code=400, detail="Accept at least one inclusion criterion before screening")
    criteria_text = "Include only if all of these apply:\n- " + "\n- ".join(inclusion)
    criteria_text += "\n\nExclude if any of these apply:\n- " + ("\n- ".join(exclusion) or "(none specified)")

    records = _load_records(db, access.project.id, body.record_ids)
    if any(record.duplicate_of_id is not None for record in records):
        raise HTTPException(status_code=400, detail="Duplicate records are not screened")

    async def call(record: models.Record) -> dict[str, Any]:
        return await evaluate_eligibility(_paper_text(record), criteria_text, body.provider)

    def store(run: models.AIRun, result: dict[str, Any]) -> None:
        quote = result.get("supporting_quote")
        run.screening = models.ScreeningSuggestion(
            decision=result["decision"],
            reasoning=str(result.get("reasoning") or ""),
            supporting_quote=None if quote is None else str(quote),
        )

    return await _run_per_record(db, access, records, "screening", body.provider, SCREENING_PROMPT_VERSION, call, store)


@router.put("/records/{record_id}/decision")
def set_screening_decision(
    record_id: int,
    body: DecisionRequest,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "screening")
    record = get_in_project(db, models.Record, record_id, access.project.id, "Record")
    if record.duplicate_of_id is not None:
        raise HTTPException(status_code=409, detail="This record is marked as a duplicate")

    existing = next(
        (d for d in record.decisions if d.stage == TITLE_ABSTRACT and d.reviewer_id == access.user.id), None
    )
    previous = existing.decision if existing else None
    if existing is not None:
        # Replace rather than update, so the newest decision always has the highest id.
        record.decisions.remove(existing)
        db.flush()
    record.decisions.append(
        models.ScreeningDecision(stage=TITLE_ABSTRACT, reviewer_id=access.user.id, decision=body.decision)
    )
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="screening.decided",
        entity_type="record",
        entity_id=record.id,
        details={"stage": TITLE_ABSTRACT, "decision": body.decision, "previous": previous},
    )
    db.commit()
    return record_out(record, access.user.id)


@router.post("/extraction/ai", dependencies=[Depends(ai_rate_limit)])
async def suggest_extraction(
    body: AIBatchRequest,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "extraction")
    fields = list(access.project.extraction_fields)
    if not fields:
        raise HTTPException(status_code=400, detail="Add at least one extraction field first")
    records = _load_records(db, access.project.id, body.record_ids)
    _require_included(records)
    field_names = [field.name for field in fields]
    fields_by_name = {field.name: field for field in fields}

    async def call(record: models.Record) -> dict[str, Any]:
        return await extract_data_from_paper(_paper_text(record), field_names, body.provider)

    def store(run: models.AIRun, result: dict[str, Any]) -> None:
        run.extraction_values = [
            models.ExtractionSuggestion(field=fields_by_name[name], value=str(value))
            for name, value in result.items()
            if name in fields_by_name
        ]

    return await _run_per_record(
        db, access, records, "extraction", body.provider, EXTRACTION_PROMPT_VERSION, call, store
    )


@router.post("/appraisal/ai", dependencies=[Depends(ai_rate_limit)])
async def suggest_appraisal(
    body: AIBatchRequest,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "appraisal")
    if access.project.protocol is None:
        raise HTTPException(status_code=404, detail="This project has no protocol")
    tool = access.project.protocol.rob_tool
    records = _load_records(db, access.project.id, body.record_ids)
    _require_included(records)

    async def call(record: models.Record) -> dict[str, Any]:
        return await assess_risk_of_bias(_paper_text(record), tool, body.provider)

    def store(run: models.AIRun, result: dict[str, Any]) -> None:
        run.appraisal = models.AppraisalSuggestion(tool=tool, judgments=result)

    return await _run_per_record(db, access, records, "appraisal", body.provider, APPRAISAL_PROMPT_VERSION, call, store)


def synthesis_out(report: models.SynthesisReport) -> dict:
    return {
        "id": report.id,
        "content": report.content,
        "record_count": report.record_count,
        "provider": report.ai_run.provider,
        "model": report.ai_run.model,
        "created_at": report.created_at,
    }


@router.post("/synthesis", status_code=201, dependencies=[Depends(ai_rate_limit)])
async def create_synthesis(
    body: SynthesisRequest,
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "synthesis")
    records = db.scalars(
        with_record_details(
            select(models.Record).where(
                models.Record.project_id == access.project.id, models.Record.duplicate_of_id.is_(None)
            )
        )
    ).all()
    included = [record_out(record, access.user.id) for record in records if final_decision(record) == "include"]
    if not included:
        raise HTTPException(status_code=400, detail="Include at least one record before generating a synthesis")

    study_data = [
        {
            "title": record["title"],
            "authors": record["authors"],
            "year": record["year"],
            "doi": record["doi"],
            "extracted_data": record["extraction"]["values"] if record["extraction"] else None,
            "risk_of_bias": record["appraisal"]["judgments"] if record["appraisal"] else None,
        }
        for record in included
    ]
    run = models.AIRun(
        project_id=access.project.id,
        task="synthesis",
        provider=body.provider,
        model=model_for(body.provider),
        prompt_version=SYNTHESIS_PROMPT_VERSION,
        triggered_by_id=access.user.id,
    )
    try:
        result = await generate_meta_analysis(study_data, body.provider)
    except LLMError as exc:
        run.status, run.error = "failed", str(exc)
        db.add(run)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    run.status = "succeeded"
    report = models.SynthesisReport(
        project_id=access.project.id, ai_run=run, content=result["report"], record_count=len(included)
    )
    db.add(report)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="ai.synthesis",
        entity_type="synthesis_report",
        entity_id=report.id,
        details={"provider": run.provider, "model": run.model, "records": len(included)},
    )
    db.commit()
    return synthesis_out(report)


@router.get("/synthesis")
def latest_synthesis(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    report = db.scalar(
        select(models.SynthesisReport)
        .where(models.SynthesisReport.project_id == access.project.id)
        .order_by(models.SynthesisReport.id.desc())
        .limit(1)
    )
    return synthesis_out(report) if report else None
